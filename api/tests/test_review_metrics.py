import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.main import app
from app.monitoring import review_metrics


def _val(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    return value if value is not None else 0.0


def test_record_review_started_increments_total():
    before = _val("review_jobs_total")
    review_metrics.record_review_started()
    assert _val("review_jobs_total") == before + 1


def test_record_review_success_updates_counters():
    before_success = _val("review_jobs_success_total")
    before_generated = _val("review_comments_generated_total")
    before_published = _val("review_comments_published_total")

    review_metrics.record_review_success(
        comments_generated=3, comments_published=2
    )

    assert _val("review_jobs_success_total") == before_success + 1
    assert _val("review_comments_generated_total") == before_generated + 3
    assert _val("review_comments_published_total") == before_published + 2


def test_record_review_success_with_zero_comments():
    before_success = _val("review_jobs_success_total")
    before_generated = _val("review_comments_generated_total")

    review_metrics.record_review_success(0, 0)

    assert _val("review_jobs_success_total") == before_success + 1
    # Counters are unchanged when there are no comments.
    assert _val("review_comments_generated_total") == before_generated


def test_record_review_failure_increments_failed():
    before = _val("review_jobs_failed_total")
    review_metrics.record_review_failure()
    assert _val("review_jobs_failed_total") == before + 1


def test_record_review_duration_observes_histogram():
    before_count = _val("review_pipeline_duration_seconds_count")
    before_sum = _val("review_pipeline_duration_seconds_sum")

    review_metrics.record_review_duration(1.5)

    assert _val("review_pipeline_duration_seconds_count") == before_count + 1
    assert _val("review_pipeline_duration_seconds_sum") == pytest.approx(
        before_sum + 1.5
    )


def test_metrics_endpoint_exposes_metrics():
    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "review_jobs_total" in response.text
