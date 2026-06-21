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


def test_reviews_health(client):
    response = client.get("/reviews/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_job_returns_state(client):
    job = job_store.create_job(repository="octocat/hello", pull_number=7)

    response = client.get(f"/reviews/jobs/{job.job_id}")
    assert response.status_code == 200

    payload = response.json()
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "pending"
    assert payload["repository"] == "octocat/hello"
    assert payload["pull_number"] == 7
    assert payload["comments_generated"] == 0


def test_get_job_reflects_updates(client):
    job = job_store.create_job(repository="octocat/hello", pull_number=7)
    job_store.update_job(job.job_id, status="completed")

    response = client.get(f"/reviews/jobs/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_missing_job_returns_404(client):
    response = client.get("/reviews/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_list_jobs(client):
    job_store.create_job(repository="octocat/a", pull_number=1, job_id="job-1")
    job_store.create_job(repository="octocat/b", pull_number=2, job_id="job-2")

    response = client.get("/reviews/jobs")
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2

    # Each item exposes the full job schema.
    for item in payload:
        assert {"job_id", "status", "repository", "comments_generated"} <= set(
            item.keys()
        )

    repositories = {item["repository"] for item in payload}
    assert repositories == {"octocat/a", "octocat/b"}


def test_list_jobs_empty(client):
    response = client.get("/reviews/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_job_detail_schema(client):
    job = job_store.create_job(repository="octocat/hello", pull_number=9)

    response = client.get(f"/reviews/jobs/{job.job_id}")
    payload = response.json()
    assert {
        "job_id",
        "status",
        "repository",
        "pull_number",
        "files_processed",
        "chunks_processed",
        "comments_generated",
        "comments_published",
        "error",
        "created_at",
        "updated_at",
    } == set(payload.keys())


def test_job_store_create_and_get_roundtrip():
    job = job_store.create_job(repository="octocat/hello", pull_number=3)
    fetched = job_store.get_job(job.job_id)

    assert fetched is not None
    assert fetched.job_id == job.job_id
    assert fetched.repository == "octocat/hello"
    assert fetched.pull_number == 3
    assert fetched.status == "pending"


def test_job_store_update_missing_returns_none():
    assert job_store.update_job("nope", status="x") is None


# --- Review trigger endpoint ---


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Replace the Celery dispatch so no broker is contacted."""
    from app.routes import reviews

    calls = {}

    def fake_apply_async(args=None, task_id=None, **kwargs):
        calls["args"] = args
        calls["task_id"] = task_id
        return None

    monkeypatch.setattr(
        reviews.review_pull_request, "apply_async", fake_apply_async
    )
    return calls


def test_trigger_review_creates_and_dispatches(client, stub_dispatch):
    response = client.post(
        "/reviews/jobs",
        json={"repository": "octocat/hello", "pull_number": 7, "installation_id": 42},
    )
    assert response.status_code == 202

    payload = response.json()
    assert payload["repository"] == "octocat/hello"
    assert payload["pull_number"] == 7
    assert payload["status"] == "queued"

    # Job persisted in the store.
    job = job_store.get_job(payload["job_id"])
    assert job is not None
    assert job.status == "queued"

    # Dispatched to Celery with the job id as the task id.
    assert stub_dispatch["args"] == ["octocat/hello", 7, 42]
    assert stub_dispatch["task_id"] == payload["job_id"]


def test_trigger_review_defaults_installation_id(client, stub_dispatch):
    response = client.post(
        "/reviews/jobs",
        json={"repository": "octocat/hello", "pull_number": 3},
    )
    assert response.status_code == 202
    assert stub_dispatch["args"] == ["octocat/hello", 3, 0]


def test_trigger_review_validates_repository(client, stub_dispatch):
    response = client.post(
        "/reviews/jobs",
        json={"repository": "not-a-repo", "pull_number": 1},
    )
    assert response.status_code == 422


def test_trigger_review_validates_pull_number(client, stub_dispatch):
    response = client.post(
        "/reviews/jobs",
        json={"repository": "octocat/hello", "pull_number": 0},
    )
    assert response.status_code == 422


def test_trigger_review_enqueue_failure_marks_failed(client, monkeypatch):
    from app.routes import reviews

    def boom(*args, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(reviews.review_pull_request, "apply_async", boom)

    response = client.post(
        "/reviews/jobs",
        json={"repository": "octocat/hello", "pull_number": 7},
    )
    assert response.status_code == 502

    # The job exists and is marked failed with the error captured.
    jobs = job_store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert "broker down" in (jobs[0].error or "")


# --- Stats moved to GraphQL (/graphql reviewStats) ---
