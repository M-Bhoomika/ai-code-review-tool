"""Client providers for the review pipeline.

The GitHub client is a real, authenticated PyGithub client. Two auth strategies
are supported, selected by the ``USE_GITHUB_APP_AUTH`` flag:

* App auth (``USE_GITHUB_APP_AUTH=true``): mints a GitHub App installation token
  for the PR's ``installation_id`` (see ``app.github.github_app_auth``).
* PAT auth (default): a static ``GITHUB_TOKEN`` (see ``app.github.auth``).

Either way the underlying PyGithub client is returned so the pipeline's existing
interfaces (``get_repo``/``get_pull``/``create_review``) keep working unchanged.

The LLM client is a real OpenAI-backed client built from ``OPENAI_API_KEY`` /
``OPENAI_MODEL`` (see ``app.llm.openai_client``). When the API key is not
configured, ``build_llm_client`` returns ``None`` and the review engine degrades
to zero comments rather than crashing the pipeline.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.github import github_app_auth
from app.github.auth import create_github_client, get_github_token
from app.llm.openai_client import OpenAIClient, load_config_from_env

logger = logging.getLogger("ai-code-review-worker.clients")


def _e2e_integration_enabled() -> bool:
    from app.e2e.stub_clients import e2e_integration_enabled

    return e2e_integration_enabled()


def build_github_client(installation_id: int) -> Optional[Any]:
    """Return an authenticated GitHub client, or None if auth is not configured.

    Uses GitHub App installation-token auth when ``USE_GITHUB_APP_AUTH`` is set;
    otherwise falls back to the static ``GITHUB_TOKEN`` PAT path.
    """
    if _e2e_integration_enabled():
        from app.e2e.stub_clients import build_stub_github_client

        client = build_stub_github_client(installation_id)
        logger.info(
            "github_client_built",
            extra={"mode": "e2e_stub", "installation_id": installation_id},
        )
        return client

    if github_app_auth.use_github_app_auth():
        try:
            client = github_app_auth.build_github_client(installation_id)
            logger.info(
                "github_client_built",
                extra={"mode": "app", "installation_id": installation_id},
            )
            return client
        except Exception as exc:  # noqa: BLE001 - degrade rather than crash
            logger.warning(
                "github_app_auth_failed",
                extra={"installation_id": installation_id, "error": str(exc)},
            )
            return None

    if not get_github_token():
        logger.warning(
            "github_token_not_configured",
            extra={"installation_id": installation_id},
        )
        return None

    logger.info(
        "github_client_built",
        extra={"mode": "pat", "installation_id": installation_id},
    )
    return create_github_client()


def build_llm_client() -> Optional[Any]:
    """Return an OpenAI LLM client exposing ``complete(prompt) -> str``.

    Returns ``None`` when ``OPENAI_API_KEY`` is not configured so the pipeline
    can run (and degrade gracefully) without an LLM.
    """
    if _e2e_integration_enabled():
        from app.e2e.stub_clients import build_stub_llm_client

        logger.info("llm_client_built", extra={"mode": "e2e_stub"})
        return build_stub_llm_client()

    config = load_config_from_env()
    if config is None:
        logger.warning("llm_client_not_configured")
        return None

    logger.info("llm_client_built", extra={"model": config.model})
    return OpenAIClient(config)
