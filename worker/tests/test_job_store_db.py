import pytest

from app.reviews import job_store


@pytest.fixture(autouse=True)
def clear_jobs():
    job_store.clear_jobs()
    yield
    job_store.clear_jobs()


def test_create_and_get_roundtrip():
    job = job_store.create_job(repository="octocat/hello", pull_number=7)
    fetched = job_store.get_job(job.job_id)

    assert fetched is not None
    assert fetched.repository == "octocat/hello"
    assert fetched.pull_number == 7
    assert fetched.status == job_store.STATUS_PENDING
    assert fetched.created_at is not None


def test_status_transitions_persist():
    job = job_store.create_job(repository="octocat/hello", pull_number=1, job_id="j")
    job_store.update_job("j", status=job_store.STATUS_RUNNING)
    assert job_store.get_job("j").status == job_store.STATUS_RUNNING

    job_store.update_job(
        "j",
        status=job_store.STATUS_COMPLETED,
        files_processed=3,
        comments_generated=2,
        comments_published=1,
    )
    done = job_store.get_job("j")
    assert done.status == job_store.STATUS_COMPLETED
    assert done.files_processed == 3
    assert done.comments_generated == 2
    assert done.comments_published == 1


def test_start_job_creates_when_missing():
    job = job_store.start_job("new-id", "octocat/hello", 9)
    assert job.status == job_store.STATUS_RUNNING
    assert job_store.get_job("new-id").pull_number == 9


def test_start_job_updates_existing_queued_row():
    job_store.create_job(
        repository="octocat/hello", pull_number=4, status="queued", job_id="q"
    )
    job_store.start_job("q", "octocat/hello", 4)
    assert job_store.get_job("q").status == job_store.STATUS_RUNNING
    assert len(job_store.list_jobs()) == 1


def test_failure_message_persisted():
    job_store.create_job(repository="octocat/hello", pull_number=2, job_id="f")
    job_store.update_job("f", status=job_store.STATUS_FAILED, error="boom")
    failed = job_store.get_job("f")
    assert failed.status == job_store.STATUS_FAILED
    assert failed.error == "boom"


def test_list_and_clear():
    job_store.create_job(repository="r/a", pull_number=1)
    job_store.create_job(repository="r/b", pull_number=2)
    assert len(job_store.list_jobs()) == 2

    job_store.clear_jobs()
    assert job_store.list_jobs() == []


def test_update_missing_returns_none():
    assert job_store.update_job("nope", status="x") is None
