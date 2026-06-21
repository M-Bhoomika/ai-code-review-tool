import uuid

import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.main import app
from app.models.repository import Repository
from app.models.review import Review
from app.models.review_comment import ReviewComment


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_analytics_data():
    with SessionLocal() as db:
        db.query(ReviewComment).delete()
        db.query(Review).delete()
        db.query(Repository).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(ReviewComment).delete()
        db.query(Review).delete()
        db.query(Repository).delete()
        db.commit()


def _graphql(client: TestClient, query: str, variables: dict | None = None):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return client.post("/graphql", json=payload)


def _persist_pipeline_review(
    *,
    repository: str = "octocat/hello-world",
    pull_number: int = 42,
    status: str = "completed",
    github_repo_id: int = 424242,
    comments: list[dict] | None = None,
) -> uuid.UUID:
    """Write analytics rows using the same shape the worker persists."""
    owner, name = repository.split("/", 1)
    review_id = uuid.uuid4()
    if comments is None:
        comments = [
            {
                "file_path": "src/main.py",
                "diff_position": 10,
                "severity": "high",
                "category": "security",
                "title": "Missing auth check",
                "explanation": "Endpoint is unauthenticated.",
                "suggested_fix": "Add auth middleware.",
                "confidence": 0.92,
            }
        ]

    with SessionLocal() as db:
        repo = Repository(
            id=uuid.uuid4(),
            github_repo_id=github_repo_id,
            owner=owner,
            name=name,
            default_branch="main",
        )
        db.add(repo)
        db.flush()

        review = Review(
            id=review_id,
            repository_id=repo.id,
            github_pr_number=pull_number,
            github_commit_sha="deadbeef",
            status=status,
            summary="Review completed with 1 findings across 3 files; 1 comment(s) published.",
            risk_score=75,
            processing_time_ms=1200,
        )
        db.add(review)

        for comment in comments:
            db.add(
                ReviewComment(
                    id=uuid.uuid4(),
                    review_id=review_id,
                    **comment,
                )
            )
        db.commit()

    return review_id


def test_graphql_returns_pipeline_persisted_review(client):
    review_id = _persist_pipeline_review()

    response = _graphql(
        client,
        """
        query {
          reviews {
            id
            repositoryName
            githubPrNumber
            status
            riskScore
            comments {
              title
              severity
              category
            }
          }
        }
        """,
    )

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    reviews = body["data"]["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["id"] == str(review_id)
    assert reviews[0]["repositoryName"] == "octocat/hello-world"
    assert reviews[0]["githubPrNumber"] == 42
    assert reviews[0]["status"] == "completed"
    assert reviews[0]["riskScore"] == 75
    assert reviews[0]["comments"][0]["title"] == "Missing auth check"


def test_graphql_stats_reflect_persisted_pipeline_data(client):
    _persist_pipeline_review()
    _persist_pipeline_review(
        repository="acme/payments",
        pull_number=7,
        status="failed",
        github_repo_id=999001,
        comments=[],
    )

    response = _graphql(
        client,
        """
        query {
          reviewStats {
            totalReviews
            totalComments
            completedReviews
            failedReviews
          }
        }
        """,
    )

    assert response.status_code == 200
    stats = response.json()["data"]["reviewStats"]
    assert stats["totalReviews"] == 2
    assert stats["totalComments"] == 1
    assert stats["completedReviews"] == 1
    assert stats["failedReviews"] == 1
