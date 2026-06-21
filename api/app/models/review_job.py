from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewJob(Base):
    """Shared, durable record of a review job's lifecycle.

    Written by the API (on trigger) and the worker (throughout execution) so
    both services observe the same job state. This is the system of record for
    job status that the dashboard and stats endpoints read from.
    """

    __tablename__ = "review_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository: Mapped[str] = mapped_column(
        String(512), nullable=False, index=True
    )
    pull_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ReviewJob {self.job_id} status={self.status}>"
