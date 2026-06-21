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


@pytest.fixture
def seeded_review_data():
    with SessionLocal() as db:
        repository = Repository(
            id=uuid.uuid4(),
            github_repo_id=12345,
            owner="octocat",
            name="hello",
            default_branch="main",
        )
        db.add(repository)
        db.flush()

        completed = Review(
            id=uuid.uuid4(),
            repository_id=repository.id,
            github_pr_number=1,
            github_commit_sha="abc111",
            status="completed",
            summary="Looks good overall",
            risk_score=20,
            processing_time_ms=1500,
        )
        pending = Review(
            id=uuid.uuid4(),
            repository_id=repository.id,
            github_pr_number=2,
            github_commit_sha="abc222",
            status="pending",
            summary=None,
            risk_score=60,
            processing_time_ms=900,
        )
        failed = Review(
            id=uuid.uuid4(),
            repository_id=repository.id,
            github_pr_number=3,
            github_commit_sha="abc333",
            status="failed",
            summary="Review failed",
            risk_score=80,
            processing_time_ms=500,
        )
        db.add_all([completed, pending, failed])
        db.flush()

        comments = [
            ReviewComment(
                id=uuid.uuid4(),
                review_id=completed.id,
                file_path="app/main.py",
                diff_position=10,
                severity="high",
                category="security",
                title="Missing auth check",
                explanation="Endpoint is unauthenticated.",
                suggested_fix="Add auth middleware.",
                confidence=0.92,
            ),
            ReviewComment(
                id=uuid.uuid4(),
                review_id=completed.id,
                file_path="app/service.py",
                diff_position=42,
                severity="medium",
                category="performance",
                title="N+1 query",
                explanation="Loop triggers repeated queries.",
                suggested_fix="Use eager loading.",
                confidence=0.81,
            ),
            ReviewComment(
                id=uuid.uuid4(),
                review_id=completed.id,
                file_path="app/logic.py",
                diff_position=7,
                severity="low",
                category="logic",
                title="Possible null dereference",
                explanation="Value may be None.",
                suggested_fix="Guard before access.",
                confidence=0.74,
            ),
            ReviewComment(
                id=uuid.uuid4(),
                review_id=completed.id,
                file_path="app/utils.py",
                diff_position=3,
                severity="high",
                category="security",
                title="Hardcoded secret",
                explanation="Secret in source code.",
                suggested_fix="Move to env var.",
                confidence=0.95,
            ),
        ]
        db.add_all(comments)
        db.commit()

        return {
            "repository": repository,
            "completed_review_id": str(completed.id),
            "pending_review_id": str(pending.id),
            "failed_review_id": str(failed.id),
        }


def _graphql(client: TestClient, query: str, variables: dict | None = None):
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return client.post("/graphql", json=payload)


def test_reviews_query_returns_persisted_reviews(client, seeded_review_data):
    response = _graphql(
        client,
        """
        query {
          reviews {
            id
            repositoryName
            githubPrNumber
            status
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
    assert len(reviews) == 3
    assert {review["status"] for review in reviews} == {
        "completed",
        "pending",
        "failed",
    }

    completed = next(
        review
        for review in reviews
        if review["id"] == seeded_review_data["completed_review_id"]
    )
    assert completed["repositoryName"] == "octocat/hello"
    assert completed["githubPrNumber"] == 1
    assert len(completed["comments"]) == 4
    assert completed["comments"][0]["severity"] in {"HIGH", "MEDIUM", "LOW", "INFO", "CRITICAL"}


def test_reviews_query_filters_by_repository(client, seeded_review_data):
    response = _graphql(
        client,
        """
        query ($repository: String) {
          reviews(repository: $repository) {
            repositoryName
          }
        }
        """,
        {"repository": "octocat/hello"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    assert len(body["data"]["reviews"]) == 3
    assert all(
        review["repositoryName"] == "octocat/hello"
        for review in body["data"]["reviews"]
    )


def test_review_stats_query(client, seeded_review_data):
    response = _graphql(
        client,
        """
        query {
          reviewStats {
            totalReviews
            totalComments
            completedReviews
            pendingReviews
            failedReviews
            averageRiskScore
            averageProcessingTimeMs
          }
        }
        """,
    )

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    stats = body["data"]["reviewStats"]
    assert stats["totalReviews"] == 3
    assert stats["totalComments"] == 4
    assert stats["completedReviews"] == 1
    assert stats["pendingReviews"] == 1
    assert stats["failedReviews"] == 1
    assert stats["averageRiskScore"] == pytest.approx(53.333333, rel=1e-3)
    assert stats["averageProcessingTimeMs"] == pytest.approx(966.666666, rel=1e-3)


def test_top_issue_categories_query(client, seeded_review_data):
    response = _graphql(
        client,
        """
        query {
          topIssueCategories(limit: 5) {
            category
            count
          }
        }
        """,
    )

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    categories = body["data"]["topIssueCategories"]
    assert categories[0] == {"category": "SECURITY", "count": 2}
    assert {"category": "PERFORMANCE", "count": 1} in categories
    assert {"category": "LOGIC", "count": 1} in categories


def test_schema_exposes_required_types(client):
    response = _graphql(
        client,
        """
        query {
          __schema {
            types {
              name
            }
          }
        }
        """,
    )

    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    type_names = {item["name"] for item in body["data"]["__schema"]["types"]}
    assert {
        "Review",
        "ReviewComment",
        "ReviewStats",
        "CategoryCount",
        "Severity",
        "Category",
    } <= type_names
