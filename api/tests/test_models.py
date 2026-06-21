import uuid
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models import Repository, Review, ReviewComment


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session for each test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def _make_repository(session: Session) -> Repository:
    repo = Repository(
        github_repo_id=123456,
        owner="octocat",
        name="hello-world",
        default_branch="main",
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)
    return repo


def test_repository_creation(session: Session) -> None:
    repo = _make_repository(session)

    assert isinstance(repo.id, uuid.UUID)
    assert repo.github_repo_id == 123456
    assert repo.owner == "octocat"
    assert repo.name == "hello-world"
    assert repo.default_branch == "main"
    assert repo.created_at is not None
    assert repo.updated_at is not None


def test_review_creation(session: Session) -> None:
    repo = _make_repository(session)

    review = Review(
        repository_id=repo.id,
        github_pr_number=42,
        github_commit_sha="abc123def456",
        status="completed",
        summary="Looks good overall.",
        risk_score=15,
        processing_time_ms=2300,
    )
    session.add(review)
    session.commit()
    session.refresh(review)

    assert isinstance(review.id, uuid.UUID)
    assert review.repository_id == repo.id
    assert review.github_pr_number == 42
    assert review.status == "completed"
    assert review.risk_score == 15
    assert review.created_at is not None


def test_relationship_loading(session: Session) -> None:
    repo = _make_repository(session)

    review = Review(
        repository_id=repo.id,
        github_pr_number=7,
        github_commit_sha="deadbeef",
        status="pending",
    )
    session.add(review)
    session.commit()

    loaded_repo = session.get(Repository, repo.id)
    assert loaded_repo is not None
    assert len(loaded_repo.reviews) == 1
    assert loaded_repo.reviews[0].github_pr_number == 7
    assert loaded_repo.reviews[0].repository is loaded_repo


def test_review_comment_creation(session: Session) -> None:
    repo = _make_repository(session)
    review = Review(
        repository_id=repo.id,
        github_pr_number=99,
        github_commit_sha="cafebabe",
        status="completed",
    )
    session.add(review)
    session.commit()
    session.refresh(review)

    comment = ReviewComment(
        review_id=review.id,
        file_path="app/main.py",
        diff_position=12,
        severity="high",
        category="security",
        title="Potential SQL injection",
        explanation="User input is concatenated into a raw SQL query.",
        suggested_fix="Use parameterized queries.",
        confidence=0.92,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)

    assert isinstance(comment.id, uuid.UUID)
    assert comment.review_id == review.id
    assert comment.severity == "high"
    assert comment.category == "security"
    assert comment.confidence == pytest.approx(0.92)

    loaded_review = session.get(Review, review.id)
    assert loaded_review is not None
    assert len(loaded_review.comments) == 1
    assert loaded_review.comments[0].title == "Potential SQL injection"
    assert loaded_review.comments[0].review is loaded_review
