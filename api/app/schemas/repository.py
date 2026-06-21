import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RepositoryBase(BaseModel):
    github_repo_id: int = Field(..., description="GitHub's numeric repository ID")
    owner: str = Field(..., max_length=255)
    name: str = Field(..., max_length=255)
    default_branch: str = Field(default="main", max_length=255)


class RepositoryCreate(RepositoryBase):
    pass


class RepositoryRead(RepositoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
