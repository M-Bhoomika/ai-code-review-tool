from contextlib import contextmanager
from typing import Optional

import strawberry
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database.session import SessionLocal
from app.graphql.enums import parse_category, parse_severity
from app.graphql.types import (
    CategoryCount,
    Review,
    ReviewComment,
    ReviewStats,
)
from app.models.repository import Repository
from app.models.review import Review as ReviewModel
from app.models.review_comment import ReviewComment as ReviewCommentModel


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _repository_name(repository: Repository) -> str:
    return f"{repository.owner}/{repository.name}"


def _to_comment(model: ReviewCommentModel) -> ReviewComment:
    return ReviewComment(
        id=model.id,
        file_path=model.file_path,
        diff_position=model.diff_position,
        severity=parse_severity(model.severity),
        category=parse_category(model.category),
        title=model.title,
        explanation=model.explanation,
        suggested_fix=model.suggested_fix,
        confidence=model.confidence,
        created_at=model.created_at,
    )


def _to_review(model: ReviewModel) -> Review:
    return Review(
        id=model.id,
        repository_id=model.repository_id,
        repository_name=_repository_name(model.repository),
        github_pr_number=model.github_pr_number,
        github_commit_sha=model.github_commit_sha,
        status=model.status,
        summary=model.summary,
        risk_score=model.risk_score,
        processing_time_ms=model.processing_time_ms,
        created_at=model.created_at,
        comments=[_to_comment(comment) for comment in model.comments],
    )


def _apply_repository_filter(query, repository: Optional[str]):
    if not repository:
        return query
    owner, _, name = repository.partition("/")
    if not name:
        return query
    return query.join(ReviewModel.repository).filter(
        Repository.owner == owner,
        Repository.name == name,
    )


def resolve_reviews(
    repository: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Review]:
    with _session() as db:
        query = (
            db.query(ReviewModel)
            .options(
                joinedload(ReviewModel.comments),
                joinedload(ReviewModel.repository),
            )
            .order_by(ReviewModel.created_at.desc())
        )
        query = _apply_repository_filter(query, repository)
        rows = query.offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all()
        return [_to_review(row) for row in rows]


def resolve_review_stats(repository: Optional[str] = None) -> ReviewStats:
    with _session() as db:
        review_query = db.query(ReviewModel)
        review_query = _apply_repository_filter(review_query, repository)

        total_reviews = review_query.count()

        comment_query = db.query(ReviewCommentModel)
        if repository:
            owner, _, name = repository.partition("/")
            if name:
                comment_query = comment_query.join(ReviewCommentModel.review).join(
                    ReviewModel.repository
                ).filter(
                    Repository.owner == owner,
                    Repository.name == name,
                )

        total_comments = comment_query.count()
        completed_reviews = review_query.filter(
            ReviewModel.status == "completed"
        ).count()
        pending_reviews = review_query.filter(
            ReviewModel.status.in_(["pending", "queued", "running"])
        ).count()
        failed_reviews = review_query.filter(ReviewModel.status == "failed").count()

        average_risk_score = review_query.with_entities(
            func.avg(ReviewModel.risk_score)
        ).scalar()
        average_processing_time_ms = review_query.with_entities(
            func.avg(ReviewModel.processing_time_ms)
        ).scalar()

        return ReviewStats(
            total_reviews=total_reviews,
            total_comments=total_comments,
            completed_reviews=completed_reviews,
            pending_reviews=pending_reviews,
            failed_reviews=failed_reviews,
            average_risk_score=(
                float(average_risk_score) if average_risk_score is not None else None
            ),
            average_processing_time_ms=(
                float(average_processing_time_ms)
                if average_processing_time_ms is not None
                else None
            ),
        )


def resolve_top_issue_categories(
    repository: Optional[str] = None,
    limit: int = 10,
) -> list[CategoryCount]:
    with _session() as db:
        query = db.query(
            ReviewCommentModel.category,
            func.count(ReviewCommentModel.id),
        )
        if repository:
            owner, _, name = repository.partition("/")
            if name:
                query = query.join(ReviewCommentModel.review).join(
                    ReviewModel.repository
                ).filter(
                    Repository.owner == owner,
                    Repository.name == name,
                )
        rows = (
            query.group_by(ReviewCommentModel.category)
            .order_by(func.count(ReviewCommentModel.id).desc())
            .limit(min(max(limit, 1), 50))
            .all()
        )
        return [
            CategoryCount(category=parse_category(category), count=count)
            for category, count in rows
        ]


@strawberry.type
class Query:
    @strawberry.field(name="reviews")
    def reviews(
        self,
        repository: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Review]:
        return resolve_reviews(repository=repository, limit=limit, offset=offset)

    @strawberry.field(name="reviewStats")
    def review_stats(self, repository: Optional[str] = None) -> ReviewStats:
        return resolve_review_stats(repository=repository)

    @strawberry.field(name="topIssueCategories")
    def top_issue_categories(
        self,
        repository: Optional[str] = None,
        limit: int = 10,
    ) -> list[CategoryCount]:
        return resolve_top_issue_categories(repository=repository, limit=limit)


schema = strawberry.Schema(query=Query)
