"""Add review_jobs table for shared job tracking

Revision ID: 0002_review_jobs
Revises: 0001_initial_schema
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_review_jobs"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_jobs",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=512), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "files_processed",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "chunks_processed",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "comments_generated",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "comments_published",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_review_jobs_repository", "review_jobs", ["repository"]
    )
    op.create_index("ix_review_jobs_status", "review_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_review_jobs_status", table_name="review_jobs")
    op.drop_index("ix_review_jobs_repository", table_name="review_jobs")
    op.drop_table("review_jobs")
