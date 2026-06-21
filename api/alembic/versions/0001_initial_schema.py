"""Initial schema: repositories, reviews, review_comments

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "default_branch",
            sa.String(length=255),
            server_default="main",
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repositories_github_repo_id",
        "repositories",
        ["github_repo_id"],
        unique=True,
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("github_pr_number", sa.Integer(), nullable=False),
        sa.Column("github_commit_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reviews_repository_id", "reviews", ["repository_id"])
    op.create_index(
        "ix_reviews_github_pr_number", "reviews", ["github_pr_number"]
    )
    op.create_index(
        "ix_reviews_github_commit_sha", "reviews", ["github_commit_sha"]
    )

    op.create_table(
        "review_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("diff_position", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_comments_review_id", "review_comments", ["review_id"]
    )
    op.create_index(
        "ix_review_comments_severity", "review_comments", ["severity"]
    )
    op.create_index(
        "ix_review_comments_category", "review_comments", ["category"]
    )


def downgrade() -> None:
    op.drop_index("ix_review_comments_category", table_name="review_comments")
    op.drop_index("ix_review_comments_severity", table_name="review_comments")
    op.drop_index("ix_review_comments_review_id", table_name="review_comments")
    op.drop_table("review_comments")

    op.drop_index("ix_reviews_github_commit_sha", table_name="reviews")
    op.drop_index("ix_reviews_github_pr_number", table_name="reviews")
    op.drop_index("ix_reviews_repository_id", table_name="reviews")
    op.drop_table("reviews")

    op.drop_index("ix_repositories_github_repo_id", table_name="repositories")
    op.drop_table("repositories")
