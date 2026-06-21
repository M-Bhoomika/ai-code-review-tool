"""Persistence-focused tests for the shared review job store.

These verify the DB-backed store the API and worker share: creation, status
transitions (as the worker would apply), and that the dashboard/stats endpoints
read the persisted state.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.reviews import job_store


@pytest.fixture(autouse=True)
def clear_job_store():
    job_store.clear_jobs()
    yield
    job_store.clear_jobs()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_job_creation_persists():
    job = job_store.create_job(repository="octocat/hello", pull_number=7)
    fetched = job_store.get_job(job.job_id)
    assert fetched is not None
    assert fetched.repository == "octocat/hello"
    assert fetched.status == "pending"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_status_transitions_queued_running_completed():
    job = job_store.create_job(
        repository="octocat/hello", pull_number=7, status="queued", job_id="j1"
    )

    job_store.update_job("j1", status="running", files_processed=2)
    assert job_store.get_job("j1").status == "running"

    job_store.update_job(
        "j1",
        status="completed",
        comments_generated=4,
        comments_published=3,
    )
    final = job_store.get_job("j1")
    assert final.status == "completed"
    assert final.files_processed == 2
    assert final.comments_generated == 4
    assert final.comments_published == 3


def test_worker_style_update_visible_via_api(client):
    """Simulate the worker writing progress; the API reads the same record."""
    job = job_store.create_job(
        repository="octocat/hello", pull_number=7, status="queued", job_id="shared"
    )
    # "Worker" updates the shared row.
    job_store.update_job(
        "shared",
        status="completed",
        files_processed=5,
        comments_generated=6,
        comments_published=4,
    )

    response = client.get(f"/reviews/jobs/{job.job_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["files_processed"] == 5
    assert payload["comments_generated"] == 6
    assert payload["comments_published"] == 4


def test_failure_message_persisted_and_readable(client):
    job = job_store.create_job(repository="octocat/hello", pull_number=7, job_id="ferr")
    job_store.update_job("ferr", status="failed", error="pipeline exploded")

    response = client.get(f"/reviews/jobs/{job.job_id}")
    assert response.json()["error"] == "pipeline exploded"
