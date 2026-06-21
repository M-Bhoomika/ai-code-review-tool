from unittest.mock import ANY, MagicMock

import pytest

from app import tasks
from app.review_pipeline import PipelineResult
from app.review_engine import ReviewComment
from app.reviews import analytics_store, job_store


@pytest.fixture(autouse=True)
def clear_jobs():
    job_store.clear_jobs()
    analytics_store.clear_analytics()
    yield
    job_store.clear_jobs()
    analytics_store.clear_analytics()


def _result(success=True, files=2, chunks=3, generated=4, published=2):
    return PipelineResult(
        repository="octocat/hello",
        pull_number=7,
        files_processed=files,
        chunks_processed=chunks,
        comments_generated=generated,
        comments_published=published,
        success=success,
        commit_sha="abc123",
        generated_comments=[
            ReviewComment(
                file_path="src/main.py",
                line_number=10,
                severity="high",
                title="Missing auth check",
                explanation="Endpoint is unauthenticated.",
                suggestion="Add auth middleware.",
            )
        ]
        if generated
        else [],
        processing_time_ms=500,
    )


def test_task_invokes_pipeline_with_clients(monkeypatch):
    pipeline_mock = MagicMock(return_value=_result())
    monkeypatch.setattr(tasks, "process_pull_request", pipeline_mock)
    monkeypatch.setattr(tasks, "build_github_client", lambda iid: f"gh-{iid}")
    monkeypatch.setattr(tasks, "build_llm_client", lambda: "llm")
    monkeypatch.setattr(tasks, "get_github_token", lambda: "tok")

    tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    pipeline_mock.assert_called_once_with(
        repository="octocat/hello",
        pull_number=7,
        github_client="gh-123",
        llm_client="llm",
        github_token="tok",
        on_progress=ANY,
    )


def test_pipeline_results_stored_in_job_store(monkeypatch):
    monkeypatch.setattr(
        tasks, "process_pull_request", MagicMock(return_value=_result())
    )

    outcome = tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    assert outcome["status"] == job_store.STATUS_COMPLETED
    assert outcome["files_processed"] == 2
    assert outcome["comments_published"] == 2

    job = job_store.get_job("job-1")
    assert job is not None
    assert job.status == job_store.STATUS_COMPLETED
    assert job.files_processed == 2
    assert job.chunks_processed == 3
    assert job.comments_generated == 4
    assert job.comments_published == 2
    assert job.error is None


def test_completed_pipeline_persists_analytics(monkeypatch):
    monkeypatch.setattr(
        tasks, "process_pull_request", MagicMock(return_value=_result())
    )

    tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    from app.database import SessionLocal
    from app.reviews.analytics_store import ReviewRecord

    with SessionLocal() as session:
        reviews = session.query(ReviewRecord).all()
        assert len(reviews) == 1
        assert reviews[0].github_pr_number == 7
        assert reviews[0].status == "completed"
        assert len(reviews[0].comments) == 1
        assert reviews[0].comments[0].title == "Missing auth check"


def test_unsuccessful_pipeline_marks_job_failed(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "process_pull_request",
        MagicMock(return_value=_result(success=False, generated=0, published=0)),
    )

    outcome = tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    assert outcome["status"] == job_store.STATUS_FAILED
    job = job_store.get_job("job-1")
    assert job.status == job_store.STATUS_FAILED


def test_pipeline_exception_is_captured(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(tasks, "process_pull_request", boom)

    # Must not raise; failure is captured and surfaced via the job store.
    outcome = tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    assert outcome["status"] == job_store.STATUS_FAILED
    assert "pipeline exploded" in outcome["error"]

    job = job_store.get_job("job-1")
    assert job.status == job_store.STATUS_FAILED
    assert job.error == "pipeline exploded"


def test_job_is_created_in_running_state_before_completion(monkeypatch):
    seen_status = {}

    def capture_status(**kwargs):
        job = job_store.get_job("job-1")
        seen_status["during"] = job.status if job else None
        return _result()

    monkeypatch.setattr(tasks, "process_pull_request", capture_status)

    tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    # While the pipeline runs, the job is in the running state.
    assert seen_status["during"] == job_store.STATUS_RUNNING


def test_worker_upserts_existing_queued_job(monkeypatch):
    # Simulate the API trigger having pre-created a queued job row.
    job_store.create_job(
        repository="octocat/hello",
        pull_number=7,
        status="queued",
        job_id="job-1",
    )
    monkeypatch.setattr(
        tasks, "process_pull_request", MagicMock(return_value=_result())
    )

    outcome = tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    # No duplicate row / no error; the existing job transitioned to completed.
    assert outcome["status"] == job_store.STATUS_COMPLETED
    assert len(job_store.list_jobs()) == 1
    assert job_store.get_job("job-1").status == job_store.STATUS_COMPLETED


def test_progress_callback_updates_job_during_execution(monkeypatch):
    captured = {}

    def fake_pipeline(**kwargs):
        on_progress = kwargs["on_progress"]
        # Emit an intermediate progress update mid-run.
        on_progress(
            PipelineResult(
                repository="octocat/hello",
                pull_number=7,
                files_processed=5,
                chunks_processed=9,
                comments_generated=0,
                comments_published=0,
                success=False,
            )
        )
        captured["mid"] = job_store.get_job("job-1")
        return _result()

    monkeypatch.setattr(tasks, "process_pull_request", fake_pipeline)

    tasks.run_review_job("octocat/hello", 7, 123, job_id="job-1")

    mid = captured["mid"]
    assert mid.status == job_store.STATUS_RUNNING
    assert mid.files_processed == 5
    assert mid.chunks_processed == 9


def test_celery_task_wrapper_uses_task_id(monkeypatch):
    run_mock = MagicMock(return_value={"status": "completed"})
    monkeypatch.setattr(tasks, "run_review_job", run_mock)

    # Eager execution runs the task locally and provides a request id.
    result = tasks.review_pull_request.apply(args=["octocat/hello", 7, 123])
    assert result.successful()

    run_mock.assert_called_once()
    call_kwargs = run_mock.call_args.kwargs
    assert call_kwargs["repository"] == "octocat/hello"
    assert call_kwargs["pull_number"] == 7
    assert call_kwargs["installation_id"] == 123
    # job_id is sourced from the Celery task id.
    assert call_kwargs["job_id"] is not None
