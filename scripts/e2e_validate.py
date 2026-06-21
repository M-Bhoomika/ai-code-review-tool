#!/usr/bin/env python3
"""End-to-end integration validation for the AI Code Review platform.

Proves the full path:
  manual trigger (+ optional webhook)
  -> Celery job queued
  -> worker processes review
  -> PostgreSQL analytics persisted
  -> GraphQL returns review data

Requires the E2E compose stack:
  docker compose -f docker-compose.e2e.yml up --build -d
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE_URL = os.getenv("E2E_API_URL", "http://localhost:8010").rstrip("/")
MLFLOW_TRACKING_URI = os.getenv(
    "MLFLOW_TRACKING_URI", "http://localhost:5001"
).rstrip("/")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "ai-code-review")
MLFLOW_RUN_WAIT_SECONDS = int(os.getenv("MLFLOW_RUN_WAIT_SECONDS", "5"))
WEBHOOK_SECRET = os.getenv("E2E_WEBHOOK_SECRET", "e2e-webhook-secret")
REPOSITORY = os.getenv("E2E_REPOSITORY", "octocat/hello")
PULL_NUMBER = int(os.getenv("E2E_PULL_NUMBER", "42"))
JOB_TIMEOUT_SECONDS = int(os.getenv("E2E_JOB_TIMEOUT_SECONDS", "120"))
POLL_INTERVAL_SECONDS = float(os.getenv("E2E_POLL_INTERVAL_SECONDS", "2"))


class E2EValidationError(RuntimeError):
    """Raised when an integration assertion fails."""


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, Any]:
    url = f"{API_BASE_URL}{path}"
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else None
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def wait_for_health() -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            status, _ = _request("GET", "/health")
            reviews_status, _ = _request("GET", "/reviews/health")
            if status == 200 and reviews_status == 200:
                print("OK  API health endpoints ready")
                return
        except urllib.error.URLError:
            pass
        time.sleep(2)
    raise E2EValidationError("API health endpoints did not become ready in time")


def trigger_manual_review() -> str:
    status, payload = _request(
        "POST",
        "/reviews/jobs",
        body={
            "repository": REPOSITORY,
            "pull_number": PULL_NUMBER,
            "installation_id": 0,
        },
    )
    if status != 202:
        raise E2EValidationError(f"Manual trigger failed ({status}): {payload}")
    job_id = payload["job_id"]
    print(f"OK  Manual review queued (job_id={job_id})")
    return job_id


def trigger_webhook_review() -> str:
    payload = {
        "action": "opened",
        "repository": {"full_name": REPOSITORY},
        "pull_request": {"number": PULL_NUMBER + 1},
        "installation": {"id": 12345},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    status, response = _request(
        "POST",
        "/webhook",
        body=payload,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        },
    )
    if status != 200 or response != {"status": "accepted"}:
        raise E2EValidationError(f"Webhook trigger failed ({status}): {response}")
    print("OK  GitHub webhook accepted")
    return "webhook-dispatched"


def wait_for_job(job_id: str) -> dict[str, Any]:
    deadline = time.time() + JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        status, payload = _request("GET", f"/reviews/jobs/{job_id}")
        if status != 200:
            raise E2EValidationError(f"Job lookup failed ({status}): {payload}")
        job_status = payload["status"]
        if job_status in {"completed", "failed"}:
            print(
                "OK  Worker finished job "
                f"(status={job_status}, generated={payload['comments_generated']}, "
                f"published={payload['comments_published']})"
            )
            if job_status != "completed":
                raise E2EValidationError(f"Expected completed job, got: {payload}")
            return payload
        time.sleep(POLL_INTERVAL_SECONDS)
    raise E2EValidationError(f"Timed out waiting for job {job_id}")


def query_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    status, payload = _request(
        "POST",
        "/graphql",
        body={"query": query, "variables": variables or {}},
    )
    if status != 200:
        raise E2EValidationError(f"GraphQL HTTP error ({status}): {payload}")
    if payload.get("errors"):
        raise E2EValidationError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def assert_graphql_analytics() -> None:
    reviews_data = query_graphql(
        """
        query ($repository: String) {
          reviews(repository: $repository) {
            repositoryName
            githubPrNumber
            status
            comments { title severity category }
          }
        }
        """,
        {"repository": REPOSITORY},
    )
    reviews = reviews_data["reviews"]
    matching = [
        review
        for review in reviews
        if review["repositoryName"] == REPOSITORY
        and review["githubPrNumber"] == PULL_NUMBER
    ]
    if not matching:
        raise E2EValidationError(
            f"GraphQL returned no review for {REPOSITORY}#{PULL_NUMBER}: {reviews}"
        )
    review = matching[0]
    if review["status"] != "completed":
        raise E2EValidationError(f"Expected completed analytics review, got: {review}")
    if not review["comments"]:
        raise E2EValidationError("Expected persisted review comments in GraphQL")
    print(
        "OK  GraphQL reviews query returned persisted data "
        f"({len(review['comments'])} comment(s))"
    )

    stats = query_graphql(
        """
        query ($repository: String) {
          reviewStats(repository: $repository) {
            totalReviews
            totalComments
            completedReviews
          }
        }
        """,
        {"repository": REPOSITORY},
    )["reviewStats"]
    if stats["totalReviews"] < 1 or stats["completedReviews"] < 1:
        raise E2EValidationError(f"Unexpected reviewStats: {stats}")
    if stats["totalComments"] < 1:
        raise E2EValidationError(f"Expected totalComments >= 1, got: {stats}")
    print(
        "OK  GraphQL reviewStats reflects persisted analytics "
        f"(totalReviews={stats['totalReviews']}, totalComments={stats['totalComments']})"
    )


def _mlflow_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{MLFLOW_TRACKING_URI}{path}{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _mlflow_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{MLFLOW_TRACKING_URI}{path}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def assert_langgraph_orchestration_used() -> None:
    """Verify the completed review ran through the LangGraph workflow."""
    time.sleep(MLFLOW_RUN_WAIT_SECONDS)
    experiment = _mlflow_get(
        "/api/2.0/mlflow/experiments/get-by-name",
        {"experiment_name": MLFLOW_EXPERIMENT_NAME},
    ).get("experiment")
    if not experiment or not experiment.get("experiment_id"):
        raise E2EValidationError(
            f"MLflow experiment '{MLFLOW_EXPERIMENT_NAME}' not found"
        )

    payload = _mlflow_post(
        "/api/2.0/mlflow/runs/search",
        {
            "experiment_ids": [experiment["experiment_id"]],
            "filter": f"params.repository = '{REPOSITORY}'",
            "max_results": 20,
            "order_by": ["attributes.start_time DESC"],
        },
    )
    for run in payload.get("runs", []):
        params = {
            item["key"]: item["value"]
            for item in run.get("data", {}).get("params", [])
        }
        if params.get("repository") != REPOSITORY:
            continue
        if int(params.get("pr_number", "0")) != PULL_NUMBER:
            continue
        if params.get("review_mode") != "langgraph":
            raise E2EValidationError(
                f"Expected MLflow review_mode=langgraph, got: {params}"
            )
        print(
            "OK  MLflow recorded LangGraph orchestration "
            f"(run_id={run['info']['run_id']}, review_mode=langgraph)"
        )
        return

    raise E2EValidationError(
        f"No MLflow run with review_mode=langgraph for {REPOSITORY}#{PULL_NUMBER}"
    )


def main() -> int:
    print(f"E2E validation against {API_BASE_URL}")
    wait_for_health()
    job_id = trigger_manual_review()
    wait_for_job(job_id)
    assert_langgraph_orchestration_used()
    trigger_webhook_review()
    assert_graphql_analytics()
    print("\nALL E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EValidationError as exc:
        print(f"\nE2E VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
