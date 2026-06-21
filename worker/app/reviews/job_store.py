"""Durable review job tracking for the worker (PostgreSQL-backed).

Reads/writes the same ``review_jobs`` table the API uses, so job status updated
during pipeline execution is visible to the API, dashboard, and stats. Returns
lightweight :class:`ReviewJob` DTOs detached from the ORM session.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, Text, DateTime, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, SessionLocal

logger = logging.getLogger("ai-code-review-worker.job_store")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewJobRecord(Base):
    """Worker ORM mapping for the shared ``review_jobs`` table."""

    __tablename__ = "review_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository: Mapped[str] = mapped_column(String(512), nullable=False)
    pull_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATUS_PENDING
    )
    files_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    chunks_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    comments_generated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    comments_published: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


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


def _to_dto(record: ReviewJobRecord) -> ReviewJob:
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
    status: str = STATUS_PENDING,
    job_id: Optional[str] = None,
) -> ReviewJob:
    new_id = job_id or str(uuid.uuid4())
    with SessionLocal() as session:
        record = ReviewJobRecord(
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
        extra={"job_id": new_id, "repository": repository, "status": status},
    )
    return dto


def start_job(
    job_id: str, repository: str, pull_number: Optional[int] = None
) -> ReviewJob:
    """Mark a job as running, creating it first if the API did not pre-create it.

    The API trigger pre-creates a ``queued`` row; webhook-dispatched runs have
    no pre-existing row. This upsert handles both paths idempotently.
    """
    with SessionLocal() as session:
        record = session.get(ReviewJobRecord, job_id)
        if record is None:
            record = ReviewJobRecord(
                job_id=job_id,
                repository=repository,
                pull_number=pull_number,
                status=STATUS_RUNNING,
            )
            session.add(record)
        else:
            record.status = STATUS_RUNNING
            if repository:
                record.repository = repository
            if pull_number is not None:
                record.pull_number = pull_number
        session.commit()
        dto = _to_dto(record)
    logger.info(
        "job_started",
        extra={"job_id": job_id, "repository": repository},
    )
    return dto


def update_job(job_id: str, **fields) -> Optional[ReviewJob]:
    with SessionLocal() as session:
        record = session.get(ReviewJobRecord, job_id)
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
    with SessionLocal() as session:
        record = session.get(ReviewJobRecord, job_id)
        return _to_dto(record) if record is not None else None


def list_jobs() -> list[ReviewJob]:
    with SessionLocal() as session:
        records = (
            session.execute(
                select(ReviewJobRecord).order_by(
                    ReviewJobRecord.created_at.asc()
                )
            )
            .scalars()
            .all()
        )
        return [_to_dto(record) for record in records]


def clear_jobs() -> None:
    with SessionLocal() as session:
        session.execute(delete(ReviewJobRecord))
        session.commit()
