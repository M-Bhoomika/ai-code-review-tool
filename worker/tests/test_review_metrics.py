from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY

from app import review_pipeline
from app.context_retriever import DiffContextBundle
from app.diff_processor import DiffChunk, DiffLine, ProcessedFile
from app.monitoring import review_metrics
from app.review_engine import ReviewComment, ReviewResult


def _val(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    return value if value is not None else 0.0


# --- Direct helper tests ---


def test_record_review_started_increments_total():
    before = _val("review_jobs_total")
    review_metrics.record_review_started()
    assert _val("review_jobs_total") == before + 1


def test_record_review_success_updates_counters():
    before_success = _val("review_jobs_success_total")
    before_generated = _val("review_comments_generated_total")
    before_published = _val("review_comments_published_total")

    review_metrics.record_review_success(
        comments_generated=4, comments_published=3
    )

    assert _val("review_jobs_success_total") == before_success + 1
    assert _val("review_comments_generated_total") == before_generated + 4
    assert _val("review_comments_published_total") == before_published + 3


def test_record_review_success_zero_comments_only_increments_success():
    before_success = _val("review_jobs_success_total")
    before_generated = _val("review_comments_generated_total")
    before_published = _val("review_comments_published_total")

    review_metrics.record_review_success(0, 0)

    assert _val("review_jobs_success_total") == before_success + 1
    assert _val("review_comments_generated_total") == before_generated
    assert _val("review_comments_published_total") == before_published


def test_record_review_failure_increments_failed():
    before = _val("review_jobs_failed_total")
    review_metrics.record_review_failure()
    assert _val("review_jobs_failed_total") == before + 1


def test_record_review_duration_observes_histogram():
    before_count = _val("review_pipeline_duration_seconds_count")
    before_sum = _val("review_pipeline_duration_seconds_sum")

    review_metrics.record_review_duration(2.0)

    assert _val("review_pipeline_duration_seconds_count") == before_count + 1
    assert _val("review_pipeline_duration_seconds_sum") == pytest.approx(
        before_sum + 2.0
    )


# --- Pipeline integration tests ---


def _processed_file(file_path="a.py", num_chunks=1):
    line = DiffLine("x=1", 1, "addition", None, 1)
    return ProcessedFile(
        file_path=file_path,
        additions=num_chunks,
        deletions=0,
        status="modified",
        chunks=[
            DiffChunk(file_path, i, [line], 1) for i in range(num_chunks)
        ],
    )


def _bundle():
    return DiffContextBundle(
        repository="octocat/hello",
        diff_file_path="a.py",
        chunk_index=0,
        diff_text="+x=1",
        retrieved_contexts=[],
    )


def _comment(title="Issue"):
    return ReviewComment("a.py", 1, "high", title, "why", "fix")


@pytest.fixture
def stub_stages(monkeypatch):
    stubs = {
        "fetch_pr_files": MagicMock(return_value=[]),
        "run_repository_indexing": MagicMock(return_value=None),
        "run_context_retrieval": MagicMock(return_value=[]),
        "run_review_generation": MagicMock(
            return_value=ReviewResult("octocat/hello", 0, [])
        ),
        "run_review_publishing": MagicMock(return_value=0),
    }
    for name, mock in stubs.items():
        monkeypatch.setattr(review_pipeline, name, mock)
    return stubs


def test_pipeline_success_records_metrics(stub_stages):
    stub_stages["fetch_pr_files"].return_value = [_processed_file("a.py", 2)]
    stub_stages["run_context_retrieval"].return_value = [_bundle()]
    stub_stages["run_review_generation"].return_value = ReviewResult(
        "octocat/hello", 2, [_comment("A"), _comment("B")]
    )
    stub_stages["run_review_publishing"].return_value = 2

    before_total = _val("review_jobs_total")
    before_success = _val("review_jobs_success_total")
    before_generated = _val("review_comments_generated_total")
    before_published = _val("review_comments_published_total")
    before_duration = _val("review_pipeline_duration_seconds_count")

    review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), MagicMock()
    )

    assert _val("review_jobs_total") == before_total + 1
    assert _val("review_jobs_success_total") == before_success + 1
    assert _val("review_comments_generated_total") == before_generated + 2
    assert _val("review_comments_published_total") == before_published + 2
    assert _val("review_pipeline_duration_seconds_count") == before_duration + 1


def test_pipeline_failure_records_metrics(stub_stages):
    stub_stages["fetch_pr_files"].side_effect = RuntimeError("github down")

    before_total = _val("review_jobs_total")
    before_failed = _val("review_jobs_failed_total")
    before_success = _val("review_jobs_success_total")
    before_duration = _val("review_pipeline_duration_seconds_count")

    result = review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), MagicMock()
    )

    assert result.success is False
    assert _val("review_jobs_total") == before_total + 1
    assert _val("review_jobs_failed_total") == before_failed + 1
    assert _val("review_jobs_success_total") == before_success
    assert _val("review_pipeline_duration_seconds_count") == before_duration + 1


def test_pipeline_empty_diff_records_success(stub_stages):
    stub_stages["fetch_pr_files"].return_value = []

    before_success = _val("review_jobs_success_total")
    before_published = _val("review_comments_published_total")

    review_pipeline.process_pull_request(
        "octocat/hello", 7, MagicMock(), MagicMock()
    )

    # Empty-diff is a graceful success with no published comments.
    assert _val("review_jobs_success_total") == before_success + 1
    assert _val("review_comments_published_total") == before_published
