"""Durable review job tracking (PostgreSQL-backed).

A thin data-access layer over the ``review_jobs`` table. The API and worker
both read/write this shared table, so job status is consistent across services.
Functions return lightweight :class:`ReviewJob` DTOs (detached from the ORM
session) so callers never deal with session lifecycle.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select

from app.database.session import SessionLocal
from app.models.review_job import ReviewJob as ReviewJobModel

logger = logging.getLogger("ai-code-review-api.job_store")

# Fields that may be mutated via update_job.
_UPDATABLE_FIELDS = {
    "status",
    "repository",
    "pull_number",
    "files_processed",
    "chunks_processed",
    "comments_generated",
    "comments_published",
    "error",
}


@dataclass
class ReviewJob:
    job_id: str
    status: str
    repository: str
    pull_number: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    files_processed: int = 0
    chunks_processed: int = 0
    comments_generated: int = 0
    comments_published: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _to_dto(record: ReviewJobModel) -> ReviewJob:
    return ReviewJob(
        job_id=record.job_id,
        status=record.status,
        repository=record.repository,
        pull_number=record.pull_number,
        created_at=_iso(record.created_at),
        updated_at=_iso(record.updated_at),
        files_processed=record.files_processed,
        chunks_processed=record.chunks_processed,
        comments_generated=record.comments_generated,
        comments_published=record.comments_published,
        error=record.error,
    )


def create_job(
    repository: str,
    pull_number: Optional[int] = None,
    status: str = "pending",
    job_id: Optional[str] = None,
) -> ReviewJob:
    """Create and persist a new review job."""
    new_id = job_id or str(uuid.uuid4())
    with SessionLocal() as session:
        record = ReviewJobModel(
            job_id=new_id,
            repository=repository,
            pull_number=pull_number,
            status=status,
        )
        session.add(record)
        session.commit()
        dto = _to_dto(record)

    logger.info(
        "job_created",
        extra={
            "job_id": new_id,
            "repository": repository,
            "pull_number": pull_number,
            "status": status,
        },
    )
    return dto


def update_job(job_id: str, **fields) -> Optional[ReviewJob]:
    """Update mutable fields of an existing job; returns None if not found."""
    with SessionLocal() as session:
        record = session.get(ReviewJobModel, job_id)
        if record is None:
            logger.warning("job_update_missing", extra={"job_id": job_id})
            return None

        applied = {}
        for key, value in fields.items():
            if key in _UPDATABLE_FIELDS:
                setattr(record, key, value)
                applied[key] = value
        session.commit()
        dto = _to_dto(record)

    logger.info("job_updated", extra={"job_id": job_id, "fields": applied})
    return dto


def get_job(job_id: str) -> Optional[ReviewJob]:
    """Return a job by id, or None if it does not exist."""
    with SessionLocal() as session:
        record = session.get(ReviewJobModel, job_id)
        return _to_dto(record) if record is not None else None


def list_jobs() -> list[ReviewJob]:
    """Return all jobs ordered by creation time (oldest first)."""
    with SessionLocal() as session:
        records = (
            session.execute(
                select(ReviewJobModel).order_by(
                    ReviewJobModel.created_at.asc()
                )
            )
            .scalars()
            .all()
        )
        return [_to_dto(record) for record in records]


def clear_jobs() -> None:
    """Remove all jobs. Intended for tests and local resets."""
    with SessionLocal() as session:
        session.execute(delete(ReviewJobModel))
        session.commit()
