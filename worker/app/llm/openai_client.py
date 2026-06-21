"""Production OpenAI LLM client for review generation.

Implements the ``LLMClient`` protocol used by the review engine
(``complete(prompt) -> str``). Configuration is read from the environment
(``OPENAI_API_KEY``, ``OPENAI_MODEL``, and optional ``OPENAI_BASE_URL``). The
``openai`` SDK is imported lazily so the worker — and the test suite — does not
require it at import time.

Failure handling:
- transient errors (rate limits, timeouts, connection/server errors) are retried
  with exponential backoff,
- unrecoverable API failures raise :class:`OpenAIError` (the review engine
  catches it and degrades to zero comments rather than crashing the pipeline),
- empty or malformed responses return an empty string (parsed as "no comments").
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("ai-code-review-worker.llm")

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_BACKOFF_SECONDS = 2.0

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"

_SYSTEM_PROMPT = (
    "You are an expert code reviewer. You respond only with the requested JSON "
    "and never include any prose outside of it."
)

# Transient/retryable OpenAI error class names. Matched by name so the openai
# package is not required to import this module (and so mocks work in tests).
_RETRYABLE_ERROR_NAMES = {
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "APIError",
}


class OpenAIError(Exception):
    """Raised when the OpenAI API call cannot be completed."""


class LLMConfigurationError(OpenAIError):
    """Raised when required LLM configuration is missing."""


@dataclass
class OpenAIConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: Optional[str] = None
    temperature: float = DEFAULT_TEMPERATURE
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS


def load_config_from_env() -> Optional[OpenAIConfig]:
    """Build config from environment variables, or None if no API key is set."""
    api_key = (os.getenv(OPENAI_API_KEY_ENV) or "").strip()
    if not api_key:
        return None
    model = (os.getenv(OPENAI_MODEL_ENV) or "").strip() or DEFAULT_MODEL
    base_url = (os.getenv(OPENAI_BASE_URL_ENV) or "").strip() or None
    return OpenAIConfig(api_key=api_key, model=model, base_url=base_url)


def _is_retryable(exc: Exception) -> bool:
    return type(exc).__name__ in _RETRYABLE_ERROR_NAMES


class OpenAIClient:
    """OpenAI-backed implementation of the review engine's ``LLMClient``."""

    def __init__(self, config: OpenAIConfig, client: Any = None):
        self._config = config
        self._client = client

    @classmethod
    def from_env(cls) -> "OpenAIClient":
        config = load_config_from_env()
        if config is None:
            raise LLMConfigurationError(
                f"{OPENAI_API_KEY_ENV} is not configured"
            )
        return cls(config)

    @property
    def model(self) -> str:
        return self._config.model

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - import guard
                raise OpenAIError("openai package is not installed") from exc
            kwargs: dict[str, Any] = {
                "api_key": self._config.api_key,
                "timeout": self._config.timeout,
            }
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _extract_content(self, response: Any) -> str:
        """Pull message content out of a chat completion, tolerating shape drift."""
        try:
            choices = response.choices
            if not choices:
                logger.warning("llm_response_no_choices")
                return ""
            content = getattr(choices[0].message, "content", None)
            if not content:
                logger.warning("llm_response_empty_content")
                return ""
            return content
        except (AttributeError, IndexError, TypeError):
            logger.warning("llm_response_malformed")
            return ""

    def complete(self, prompt: str) -> str:
        """Send the prompt to OpenAI and return the raw response text.

        Retries transient failures with exponential backoff. Raises
        :class:`OpenAIError` on unrecoverable failure; returns an empty string
        for empty/malformed responses.
        """
        client = self._ensure_client()
        attempts = max(1, self._config.max_retries)
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                response = client.chat.completions.create(
                    model=self._config.model,
                    temperature=self._config.temperature,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
            except Exception as exc:  # noqa: BLE001 - classify then retry/raise
                last_exc = exc
                if _is_retryable(exc) and attempt < attempts:
                    delay = self._config.backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "llm_request_retry",
                        extra={
                            "attempt": attempt,
                            "delay": delay,
                            "error": str(exc),
                        },
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    "llm_request_failed",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                raise OpenAIError(str(exc)) from exc

            return self._extract_content(response)

        raise OpenAIError(  # pragma: no cover - loop always returns/raises
            str(last_exc) if last_exc else "unknown LLM failure"
        )
