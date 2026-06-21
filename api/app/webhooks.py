import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.celery_client import review_pull_request
from app.config import settings

logger = logging.getLogger(settings.service_name)

router = APIRouter()

# Pull request actions that should trigger a review.
SUPPORTED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def _verify_signature(payload_body: bytes, signature_header: str | None) -> None:
    """Validate the GitHub ``X-Hub-Signature-256`` header.

    Raises HTTP 403 for a missing or invalid signature.
    """
    if not signature_header:
        logger.warning("Webhook rejected: missing signature header")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing signature",
        )

    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        logger.error("GITHUB_WEBHOOK_SECRET is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret not configured",
        )

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        logger.warning("Webhook rejected: invalid signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature",
        )


@router.post("/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    if x_github_event != "pull_request":
        logger.info(
            "Ignoring unsupported event",
            extra={"event": x_github_event},
        )
        return {"status": "ignored"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    action = payload.get("action")
    if action not in SUPPORTED_PR_ACTIONS:
        logger.info(
            "Ignoring unsupported pull_request action",
            extra={"action": action},
        )
        return {"status": "ignored"}

    try:
        repository = payload["repository"]["full_name"]
        pr_number = payload["pull_request"]["number"]
        installation_id = payload["installation"]["id"]
    except (KeyError, TypeError) as exc:
        logger.warning("Webhook payload missing required fields")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed pull_request payload",
        ) from exc

    review_pull_request.delay(repository, pr_number, installation_id)
    logger.info(
        "Dispatched pull request review",
        extra={
            "repository": repository,
            "pr_number": pr_number,
            "installation_id": installation_id,
            "action": action,
        },
    )
    return {"status": "accepted"}
