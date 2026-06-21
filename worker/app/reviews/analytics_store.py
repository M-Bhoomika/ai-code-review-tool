"""Persist completed review analytics for the GraphQL API.

Writes to the shared ``repositories``, ``reviews``, and ``review_comments``
tables that the API's GraphQL resolvers read. The worker owns the write path;
the API only serves queries.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    select,
)
from sqlalchemy.orm import Mapped, joinedload, mapped_column, relationship

from app.database import Base, SessionLocal
from app.review_engine import ReviewComment

logger = logging.getLogger("ai-code-review-worker.analytics_store")

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_SEVERITY_WEIGHT = {
    "critical": 100,
    "high": 75,
    "medium": 50,
    "low": 25,
    "info": 10,
}

_DEFAULT_CONFIDENCE = 0.85


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RepositoryRecord(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(255), nullable=False, default="main"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    reviews: Mapped[list["ReviewRecord"]] = relationship(back_populates="repository")


class ReviewRecord(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    github_pr_number: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    github_commit_sha: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    repository: Mapped["RepositoryRecord"] = relationship(back_populates="reviews")
    comments: Mapped[list["ReviewCommentRecord"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
    )


class ReviewCommentRecord(Base):
    __tablename__ = "review_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    diff_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_fix: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    review: Mapped["ReviewRecord"] = relationship(back_populates="comments")


@dataclass
class RepositoryMetadata:
    github_repo_id: int
    owner: str
    name: str
    default_branch: str = "main"


def resolve_repository_metadata(
    repository: str, github_client: object | None = None
) -> RepositoryMetadata:
    """Resolve repository identity for analytics persistence."""
    owner, _, name = repository.partition("/")
    if not name:
        name = owner
        owner = "unknown"

    github_repo_id = abs(hash(repository)) % (10**15)
    default_branch = "main"

    if github_client is not None:
        try:
            repo = github_client.get_repo(repository)
            github_repo_id = int(repo.id)
            default_branch = getattr(repo, "default_branch", None) or "main"
            owner = getattr(repo, "owner", None)
            if owner is not None and hasattr(owner, "login"):
                owner = owner.login
            name = repo.name
        except Exception as exc:  # noqa: BLE001 - fall back to parsed values
            logger.warning(
                "repository_metadata_lookup_failed",
                extra={"repository": repository, "error": str(exc)},
            )

    return RepositoryMetadata(
        github_repo_id=github_repo_id,
        owner=str(owner),
        name=str(name),
        default_branch=default_branch,
    )


def infer_category(comment: ReviewComment) -> str:
    """Map a generated comment to a persisted analytics category."""
    text = f"{comment.title} {comment.explanation}".lower()
    if any(token in text for token in ("security", "auth", "secret", "injection", "xss")):
        return "security"
    if any(token in text for token in ("performance", "n+1", "slow", "latency", "memory")):
        return "performance"
    if any(token in text for token in ("bug", "crash", "null", "undefined", "logic")):
        return "logic"
    if any(token in text for token in ("maintainability", "duplicate", "complexity")):
        return "maintainability"
    if any(token in text for token in ("quality", "style", "readability")):
        return "code_quality"
    return "other"


def compute_risk_score(comments: Sequence[ReviewComment]) -> int:
    if not comments:
        return 0
    weights = [
        _SEVERITY_WEIGHT.get((comment.severity or "info").lower(), 10)
        for comment in comments
    ]
    return min(100, max(weights))


def build_summary(
    comments: Sequence[ReviewComment], files_processed: int, published: int
) -> str:
    if not comments:
        return f"Review completed with no findings across {files_processed} files."
    return (
        f"Review completed with {len(comments)} findings across "
        f"{files_processed} files; {published} comment(s) published."
    )


def _get_or_create_repository(
    session, metadata: RepositoryMetadata
) -> RepositoryRecord:
    existing = session.execute(
        select(RepositoryRecord).where(
            RepositoryRecord.github_repo_id == metadata.github_repo_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.owner = metadata.owner
        existing.name = metadata.name
        existing.default_branch = metadata.default_branch
        existing.updated_at = _utcnow()
        return existing

    record = RepositoryRecord(
        github_repo_id=metadata.github_repo_id,
        owner=metadata.owner,
        name=metadata.name,
        default_branch=metadata.default_branch,
    )
    session.add(record)
    session.flush()
    return record


def persist_review_analytics(
    *,
    repository: str,
    pull_number: int,
    commit_sha: str,
    status: str,
    comments: Sequence[ReviewComment],
    files_processed: int,
    comments_published: int,
    processing_time_ms: int,
    github_client: object | None = None,
    summary: Optional[str] = None,
) -> uuid.UUID:
    """Persist one review and its findings for GraphQL analytics."""
    metadata = resolve_repository_metadata(repository, github_client)
    review_id = uuid.uuid4()
    normalized_status = status if status in {STATUS_COMPLETED, STATUS_FAILED} else STATUS_FAILED
    commit = commit_sha or "unknown"
    review_summary = summary or build_summary(comments, files_processed, comments_published)
    risk_score = compute_risk_score(comments)

    with SessionLocal() as session:
        repo_record = _get_or_create_repository(session, metadata)
        review_record = ReviewRecord(
            id=review_id,
            repository_id=repo_record.id,
            github_pr_number=pull_number,
            github_commit_sha=commit,
            status=normalized_status,
            summary=review_summary,
            risk_score=risk_score,
            processing_time_ms=processing_time_ms,
        )
        session.add(review_record)

        for comment in comments:
            session.add(
                ReviewCommentRecord(
                    review_id=review_id,
                    file_path=comment.file_path,
                    diff_position=comment.line_number,
                    severity=(comment.severity or "info").lower(),
                    category=infer_category(comment),
                    title=comment.title,
                    explanation=comment.explanation,
                    suggested_fix=comment.suggestion or None,
                    confidence=_DEFAULT_CONFIDENCE,
                )
            )

        session.commit()

    logger.info(
        "analytics_persisted",
        extra={
            "review_id": str(review_id),
            "repository": repository,
            "pull_number": pull_number,
            "status": normalized_status,
            "comments": len(comments),
        },
    )
    return review_id


def clear_analytics() -> None:
    """Delete all analytics rows (test helper)."""
    with SessionLocal() as session:
        session.query(ReviewCommentRecord).delete()
        session.query(ReviewRecord).delete()
        session.query(RepositoryRecord).delete()
        session.commit()


def get_review(review_id: uuid.UUID) -> ReviewRecord | None:
    with SessionLocal() as session:
        return session.execute(
            select(ReviewRecord)
            .options(joinedload(ReviewRecord.comments))
            .where(ReviewRecord.id == review_id)
        ).unique().scalar_one_or_none()
