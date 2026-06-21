#!/usr/bin/env python3
"""Validate MLflow records review pipeline runs.

Requires the platform stack with MLflow running:

  docker compose up --build -d
  python scripts/mlflow_validate.py

Or against the E2E stack (API on 8010, MLflow on 5001):

  docker compose -f docker-compose.e2e.yml up --build -d
  E2E_API_URL=http://localhost:8010 MLFLOW_TRACKING_URI=http://localhost:5001 \\
    python scripts/mlflow_validate.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE_URL = os.getenv("E2E_API_URL", "http://localhost:8000").rstrip("/")
MLFLOW_TRACKING_URI = os.getenv(
    "MLFLOW_TRACKING_URI", "http://localhost:5000"
).rstrip("/")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "ai-code-review")
REPOSITORY = os.getenv("E2E_REPOSITORY", "octocat/hello")
PULL_NUMBER = int(os.getenv("E2E_PULL_NUMBER", "42"))
JOB_TIMEOUT_SECONDS = int(os.getenv("E2E_JOB_TIMEOUT_SECONDS", "120"))
RUN_WAIT_SECONDS = int(os.getenv("MLFLOW_RUN_WAIT_SECONDS", "5"))

REQUIRED_PARAMS = ("repository", "pr_number", "model_name", "review_mode")
REQUIRED_METRICS = (
    "processing_time",
    "total_findings",
    "security_findings",
    "performance_findings",
    "logic_findings",
    "comments_published",
)
REQUIRED_ARTIFACTS = ("review_summary.txt", "findings.json")


class MLflowValidationError(RuntimeError):
    """Raised when MLflow validation fails."""


def _mlflow_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{MLFLOW_TRACKING_URI}{path}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _mlflow_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{MLFLOW_TRACKING_URI}{path}{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def assert_mlflow_ready() -> None:
    try:
        _mlflow_get(
            "/api/2.0/mlflow/experiments/get-by-name",
            {"experiment_name": MLFLOW_EXPERIMENT_NAME},
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    print(f"OK  MLflow tracking server ready ({MLFLOW_TRACKING_URI})")


def trigger_review() -> str:
    payload = {
        "repository": REPOSITORY,
        "pull_number": PULL_NUMBER,
        "installation_id": 0,
    }
    request = urllib.request.Request(
        f"{API_BASE_URL}/reviews/jobs",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
        if response.status != 202:
            raise MLflowValidationError(f"Manual trigger failed: {body}")
        return body["job_id"]


def wait_for_job(job_id: str) -> None:
    deadline = time.time() + JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        with urllib.request.urlopen(
            f"{API_BASE_URL}/reviews/jobs/{job_id}", timeout=30
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if payload["status"] in {"completed", "failed"}:
                if payload["status"] != "completed":
                    raise MLflowValidationError(
                        f"Review job did not complete: {payload}"
                    )
                print(
                    "OK  Review job completed "
                    f"(generated={payload['comments_generated']}, "
                    f"published={payload['comments_published']})"
                )
                return
        time.sleep(2)
    raise MLflowValidationError(f"Timed out waiting for job {job_id}")


def get_experiment_id() -> str:
    payload = _mlflow_get(
        "/api/2.0/mlflow/experiments/get-by-name",
        {"experiment_name": MLFLOW_EXPERIMENT_NAME},
    )
    experiment = payload.get("experiment")
    if not experiment or not experiment.get("experiment_id"):
        raise MLflowValidationError(
            f"Experiment '{MLFLOW_EXPERIMENT_NAME}' not found in MLflow"
        )
    experiment_id = experiment["experiment_id"]
    print(f"OK  MLflow experiment '{MLFLOW_EXPERIMENT_NAME}' (id={experiment_id})")
    return experiment_id


def find_matching_run(experiment_id: str) -> dict[str, Any]:
    payload = _mlflow_post(
        "/api/2.0/mlflow/runs/search",
        {
            "experiment_ids": [experiment_id],
            "filter": f"params.repository = '{REPOSITORY}'",
            "max_results": 20,
            "order_by": ["attributes.start_time DESC"],
        },
    )
    runs = payload.get("runs", [])
    for run in runs:
        params = {
            item["key"]: item["value"]
            for item in run.get("data", {}).get("params", [])
        }
        if params.get("repository") != REPOSITORY:
            continue
        if int(params.get("pr_number", "0")) != PULL_NUMBER:
            continue
        return run

    raise MLflowValidationError(
        f"No MLflow run found for {REPOSITORY}#{PULL_NUMBER}"
    )


def assert_run_contents(run: dict[str, Any]) -> None:
    data = run.get("data", {})
    params = {item["key"]: item["value"] for item in data.get("params", [])}
    metrics = {item["key"]: item["value"] for item in data.get("metrics", [])}

    missing_params = [name for name in REQUIRED_PARAMS if name not in params]
    if missing_params:
        raise MLflowValidationError(f"Run missing params: {missing_params}")

    if params["repository"] != REPOSITORY:
        raise MLflowValidationError(
            f"Expected repository={REPOSITORY}, got {params['repository']}"
        )
    if int(params["pr_number"]) != PULL_NUMBER:
        raise MLflowValidationError(
            f"Expected pr_number={PULL_NUMBER}, got {params['pr_number']}"
        )

    missing_metrics = [name for name in REQUIRED_METRICS if name not in metrics]
    if missing_metrics:
        raise MLflowValidationError(f"Run missing metrics: {missing_metrics}")

    print(
        "OK  MLflow run contains required params and metrics "
        f"(run_id={run['info']['run_id']})"
    )
    print(
        "    metrics: "
        f"processing_time={metrics['processing_time']}, "
        f"total_findings={metrics['total_findings']}, "
        f"comments_published={metrics['comments_published']}"
    )


def assert_run_artifacts(run_id: str) -> None:
    payload = _mlflow_get(
        "/api/2.0/mlflow/artifacts/list",
        {"run_id": run_id},
    )
    files = payload.get("files", [])
    artifact_paths = {item.get("path") for item in files}
    missing = [name for name in REQUIRED_ARTIFACTS if name not in artifact_paths]
    if missing:
        raise MLflowValidationError(
            f"Run missing artifacts {missing}; found {sorted(artifact_paths)}"
        )
    print(f"OK  MLflow run artifacts present: {', '.join(REQUIRED_ARTIFACTS)}")


def main() -> int:
    print(
        "MLflow validation against "
        f"API={API_BASE_URL} MLflow={MLFLOW_TRACKING_URI}"
    )
    try:
        with urllib.request.urlopen(f"{API_BASE_URL}/health", timeout=30):
            print("OK  API health")
        assert_mlflow_ready()
    except urllib.error.URLError as exc:
        raise MLflowValidationError(f"Service unavailable: {exc}") from exc

    job_id = trigger_review()
    print(f"OK  Review queued (job_id={job_id})")
    wait_for_job(job_id)

    time.sleep(RUN_WAIT_SECONDS)
    experiment_id = get_experiment_id()
    run = find_matching_run(experiment_id)
    assert_run_contents(run)
    assert_run_artifacts(run["info"]["run_id"])

    print("\nALL MLFLOW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MLflowValidationError as exc:
        print(f"\nMLFLOW VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
