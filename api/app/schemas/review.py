import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReviewCommentBase(BaseModel):
    file_path: str = Field(..., max_length=1024)
    diff_position: int | None = None
    severity: str = Field(..., max_length=32)
    category: str = Field(..., max_length=64)
    title: str = Field(..., max_length=512)
    explanation: str
    suggested_fix: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class ReviewCommentCreate(ReviewCommentBase):
    pass


class ReviewCommentRead(ReviewCommentBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_id: uuid.UUID
    created_at: datetime


class ReviewBase(BaseModel):
    github_pr_number: int
    github_commit_sha: str = Field(..., max_length=64)
    status: str = Field(default="pending", max_length=32)
    summary: str | None = None
    risk_score: int | None = None
    processing_time_ms: int | None = None


class ReviewCreate(ReviewBase):
    repository_id: uuid.UUID


class ReviewRead(ReviewBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    created_at: datetime
    comments: list[ReviewCommentRead] = Field(default_factory=list)
