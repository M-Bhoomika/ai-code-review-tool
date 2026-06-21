import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from app.celery_app import celery_app
from app.clients import build_github_client, build_llm_client
from app.github.auth import get_github_token
from app.monitoring import tracing
from app.review_pipeline import process_pull_request
from app.reviews import analytics_store, job_store

logger = logging.getLogger("ai-code-review-worker")


@dataclass
class ReviewJobRequest:
    """Input for a pull request review job."""

    repository: str
    pull_number: int
    installation_id: int


@celery_app.task(name="health_check")
def health_check() -> dict[str, str]:
    """Simple task used to verify the worker is processing jobs."""
    return {"status": "ok", "service": "worker"}


def run_review_job(
    repository: str,
    pull_number: int,
    installation_id: int,
    job_id: Optional[str] = None,
) -> dict[str, object]:
    """Execute the review pipeline for a PR and record status in the job store.

    Returns a JSON-serializable result describing the job outcome. Failures are
    captured (never raised) and surfaced via the job's ``failed`` status.
    """
    request = ReviewJobRequest(
        repository=repository,
        pull_number=pull_number,
        installation_id=installation_id,
    )
    job_id = job_id or str(uuid.uuid4())

    with tracing.span(
        "review.job",
        job_id=job_id,
        repository=request.repository,
        pull_number=request.pull_number,
        installation_id=request.installation_id,
    ):
        return _execute_review_job(request, job_id)


def _execute_review_job(request: ReviewJobRequest, job_id: str) -> dict[str, object]:
    # Upsert: the API trigger may have already created a queued row; webhook
    # dispatches have not. Either way the job is now marked running.
    job_store.start_job(
        job_id=job_id,
        repository=request.repository,
        pull_number=request.pull_number,
    )
    logger.info(
        "review_job_started",
        extra={
            "job_id": job_id,
            "repository": request.repository,
            "pull_number": request.pull_number,
            "installation_id": request.installation_id,
        },
    )

    def _on_progress(partial) -> None:
        job_store.update_job(
            job_id,
            status=job_store.STATUS_RUNNING,
            files_processed=partial.files_processed,
            chunks_processed=partial.chunks_processed,
            comments_generated=partial.comments_generated,
            comments_published=partial.comments_published,
        )

    try:
        github_client = build_github_client(request.installation_id)
        llm_client = build_llm_client()

        result = process_pull_request(
            repository=request.repository,
            pull_number=request.pull_number,
            github_client=github_client,
            llm_client=llm_client,
            github_token=get_github_token(),
            on_progress=_on_progress,
        )

        status = (
            job_store.STATUS_COMPLETED
            if result.success
            else job_store.STATUS_FAILED
        )
        job_store.update_job(
            job_id,
            status=status,
            files_processed=result.files_processed,
            chunks_processed=result.chunks_processed,
            comments_generated=result.comments_generated,
            comments_published=result.comments_published,
        )
        analytics_store.persist_review_analytics(
            repository=request.repository,
            pull_number=request.pull_number,
            commit_sha=result.commit_sha or "unknown",
            status=status,
            comments=result.generated_comments,
            files_processed=result.files_processed,
            comments_published=result.comments_published,
            processing_time_ms=result.processing_time_ms,
            github_client=github_client,
        )
        logger.info(
            "review_job_finished",
            extra={
                "job_id": job_id,
                "repository": request.repository,
                "pull_number": request.pull_number,
                "status": status,
            },
        )
        return {
            "job_id": job_id,
            "status": status,
            "repository": request.repository,
            "pull_number": request.pull_number,
            "files_processed": result.files_processed,
            "chunks_processed": result.chunks_processed,
            "comments_generated": result.comments_generated,
            "comments_published": result.comments_published,
        }
    except Exception as exc:  # noqa: BLE001 - capture and surface as a failed job
        job_store.update_job(
            job_id, status=job_store.STATUS_FAILED, error=str(exc)
        )
        analytics_store.persist_review_analytics(
            repository=request.repository,
            pull_number=request.pull_number,
            commit_sha="unknown",
            status=job_store.STATUS_FAILED,
            comments=[],
            files_processed=0,
            comments_published=0,
            processing_time_ms=0,
            github_client=None,
            summary=f"Review failed: {exc}",
        )
        logger.error(
            "review_job_failed",
            extra={
                "job_id": job_id,
                "repository": request.repository,
                "pull_number": request.pull_number,
                "error": str(exc),
            },
        )
        return {
            "job_id": job_id,
            "status": job_store.STATUS_FAILED,
            "repository": request.repository,
            "pull_number": request.pull_number,
            "error": str(exc),
        }


@celery_app.task(name="review_pull_request", bind=True)
def review_pull_request(
    self, repository: str, pr_number: int, installation_id: int
) -> dict[str, object]:
    """Celery entrypoint: run the review pipeline for a dispatched PR.

    The Celery task id is reused as the job id so the run is traceable.
    """
    job_id = getattr(self.request, "id", None)
    return run_review_job(
        repository=repository,
        pull_number=pr_number,
        installation_id=installation_id,
        job_id=job_id,
    )
