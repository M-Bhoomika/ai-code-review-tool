import uuid

import pytest

from app.review_engine import ReviewComment
from app.reviews import analytics_store


@pytest.fixture(autouse=True)
def clear_analytics():
    analytics_store.clear_analytics()
    yield
    analytics_store.clear_analytics()


def test_persist_review_analytics_writes_comments():
    review_id = analytics_store.persist_review_analytics(
        repository="octocat/hello-world",
        pull_number=42,
        commit_sha="abc123",
        status=analytics_store.STATUS_COMPLETED,
        comments=[
            ReviewComment(
                file_path="src/main.py",
                line_number=12,
                severity="high",
                title="Missing auth check",
                explanation="Endpoint is unauthenticated.",
                suggestion="Add auth middleware.",
            )
        ],
        files_processed=3,
        comments_published=1,
        processing_time_ms=900,
    )

    review = analytics_store.get_review(review_id)
    assert review is not None
    assert review.github_pr_number == 42
    assert review.status == "completed"
    assert review.github_commit_sha == "abc123"
    assert len(review.comments) == 1
    assert review.comments[0].category == "security"
    assert review.comments[0].title == "Missing auth check"


def test_infer_category_maps_security_keywords():
    comment = ReviewComment(
        file_path="a.py",
        line_number=1,
        severity="high",
        title="Hardcoded secret",
        explanation="API key committed to source control.",
        suggestion="Use env vars.",
    )
    assert analytics_store.infer_category(comment) == "security"


def test_compute_risk_score_uses_highest_severity():
    comments = [
        ReviewComment("a.py", 1, "low", "Minor", "e", "f"),
        ReviewComment("b.py", 2, "critical", "Major", "e", "f"),
    ]
    assert analytics_store.compute_risk_score(comments) == 100
