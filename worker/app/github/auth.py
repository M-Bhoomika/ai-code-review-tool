"""Worker-side GitHub authentication.

Centralizes how the worker authenticates to GitHub so auth logic is not
scattered across modules. The worker authenticates with a token read from the
environment (``GITHUB_TOKEN``); an optional ``GITHUB_API_URL`` supports GitHub
Enterprise.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from github import Auth, Github

logger = logging.getLogger("ai-code-review-worker.github")

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
GITHUB_API_URL_ENV = "GITHUB_API_URL"


def get_github_token() -> Optional[str]:
    """Return the configured GitHub token, or None if unset/blank."""
    token = (os.getenv(GITHUB_TOKEN_ENV) or "").strip()
    return token or None


def create_github_client(token: Optional[str] = None) -> Github:
    """Create an authenticated PyGithub client.

    Uses the provided token or falls back to ``GITHUB_TOKEN``. Raises
    RuntimeError if no token is available.
    """
    token = token or get_github_token()
    if not token:
        raise RuntimeError(
            f"{GITHUB_TOKEN_ENV} is not configured; cannot authenticate to GitHub"
        )

    auth = Auth.Token(token)
    base_url = (os.getenv(GITHUB_API_URL_ENV) or "").strip()
    if base_url:
        logger.info("github_client_created", extra={"base_url": base_url})
        return Github(auth=auth, base_url=base_url)

    logger.info("github_client_created", extra={"base_url": "default"})
    return Github(auth=auth)
