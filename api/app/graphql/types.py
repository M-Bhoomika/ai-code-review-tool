from datetime import datetime
from typing import Optional
from uuid import UUID

import strawberry

from app.graphql.enums import Category, Severity


@strawberry.type
class ReviewComment:
    id: UUID
    file_path: str
    diff_position: Optional[int]
    severity: Severity
    category: Category
    title: str
    explanation: str
    suggested_fix: Optional[str]
    confidence: float
    created_at: datetime


@strawberry.type
class Review:
    id: UUID
    repository_id: UUID
    repository_name: str
    github_pr_number: int
    github_commit_sha: str
    status: str
    summary: Optional[str]
    risk_score: Optional[int]
    processing_time_ms: Optional[int]
    created_at: datetime
    comments: list[ReviewComment]


@strawberry.type
class ReviewStats:
    total_reviews: int
    total_comments: int
    completed_reviews: int
    pending_reviews: int
    failed_reviews: int
    average_risk_score: Optional[float]
    average_processing_time_ms: Optional[float]


@strawberry.type
class CategoryCount:
    category: Category
    count: int
