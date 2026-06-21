import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.celery_client import review_pull_request
from app.reviews import job_store

logger = logging.getLogger("ai-code-review-api.reviews")

router = APIRouter(prefix="/reviews")

STATUS_QUEUED = "queued"


class HealthResponse(BaseModel):
    status: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    repository: str
    pull_number: Optional[int] = None
    files_processed: int = 0
    chunks_processed: int = 0
    comments_generated: int = 0
    comments_published: int = 0
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TriggerReviewRequest(BaseModel):
    repository: str = Field(..., min_length=3, examples=["octocat/hello-world"])
    pull_number: int = Field(..., gt=0)
    installation_id: int = Field(default=0, ge=0)

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        cleaned = value.strip()
        if "/" not in cleaned or cleaned.startswith("/") or cleaned.endswith("/"):
            raise ValueError("repository must be in 'owner/name' format")
        return cleaned


def _to_response(job: job_store.ReviewJob) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        repository=job.repository,
        pull_number=job.pull_number,
        files_processed=job.files_processed,
        chunks_processed=job.chunks_processed,
        comments_generated=job.comments_generated,
        comments_published=job.comments_published,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/health", response_model=HealthResponse)
async def reviews_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_review(request: TriggerReviewRequest) -> JobResponse:
    """Start a review job for a repository/PR and dispatch it to the worker."""
    job = job_store.create_job(
        repository=request.repository,
        pull_number=request.pull_number,
        status=STATUS_QUEUED,
    )
    try:
        # Reuse the job id as the Celery task id so the dispatched run is
        # traceable back to this job.
        review_pull_request.apply_async(
            args=[request.repository, request.pull_number, request.installation_id],
            task_id=job.job_id,
        )
    except Exception as exc:  # noqa: BLE001 - surface enqueue failures clearly
        job_store.update_job(job.job_id, status="failed", error=str(exc))
        logger.error(
            "review_enqueue_failed",
            extra={"job_id": job.job_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to enqueue review job",
        ) from exc

    logger.info(
        "review_triggered",
        extra={
            "job_id": job.job_id,
            "repository": request.repository,
            "pull_number": request.pull_number,
        },
    )
    return _to_response(job)


@router.get("/jobs", response_model=list[JobResponse])
async def list_review_jobs() -> list[JobResponse]:
    # Newest first for a dashboard-friendly ordering.
    jobs = list(reversed(job_store.list_jobs()))
    logger.info("jobs_list_requested", extra={"count": len(jobs)})
    return [_to_response(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_review_job(job_id: str) -> JobResponse:
    job = job_store.get_job(job_id)
    logger.info(
        "job_requested",
        extra={"job_id": job_id, "found": job is not None},
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return _to_response(job)