#!/usr/bin/env python3
"""Validate OpenTelemetry traces reach Jaeger after a review run.

Requires the platform stack with Jaeger running:

  docker compose up --build -d
  python scripts/tracing_validate.py

Or against the E2E stack (Jaeger UI on port 16687):

  docker compose -f docker-compose.e2e.yml up --build -d
  E2E_API_URL=http://localhost:8010 JAEGER_QUERY_URL=http://localhost:16687 \\
    python scripts/tracing_validate.py
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
JAEGER_QUERY_URL = os.getenv("JAEGER_QUERY_URL", "http://localhost:16686").rstrip("/")
REPOSITORY = os.getenv("E2E_REPOSITORY", "octocat/hello")
PULL_NUMBER = int(os.getenv("E2E_PULL_NUMBER", "42"))
JOB_TIMEOUT_SECONDS = int(os.getenv("E2E_JOB_TIMEOUT_SECONDS", "120"))
TRACE_WAIT_SECONDS = int(os.getenv("TRACE_WAIT_SECONDS", "10"))


class TracingValidationError(RuntimeError):
    """Raised when Jaeger does not contain expected traces."""


def _request(method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def trigger_review() -> str:
    status_url = f"{API_BASE_URL}/reviews/jobs"
    payload = {
        "repository": REPOSITORY,
        "pull_number": PULL_NUMBER,
        "installation_id": 0,
    }
    request = urllib.request.Request(
        status_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
        if response.status != 202:
            raise TracingValidationError(f"Manual trigger failed: {body}")
        return body["job_id"]


def wait_for_job(job_id: str) -> None:
    deadline = time.time() + JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        url = f"{API_BASE_URL}/reviews/jobs/{job_id}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if payload["status"] in {"completed", "failed"}:
                if payload["status"] != "completed":
                    raise TracingValidationError(
                        f"Review job did not complete: {payload}"
                    )
                print(
                    "OK  Review job completed "
                    f"(generated={payload['comments_generated']})"
                )
                return
        time.sleep(2)
    raise TracingValidationError(f"Timed out waiting for job {job_id}")


def _jaeger_traces(service: str) -> list[dict[str, Any]]:
    url = (
        f"{JAEGER_QUERY_URL}/api/traces"
        f"?service={urllib.parse.quote(service)}&limit=20"
    )
    payload = _request("GET", url)
    return payload.get("data", [])


def assert_traces_present() -> None:
    services_url = f"{JAEGER_QUERY_URL}/api/services"
    services_payload = _request("GET", services_url)
    services = services_payload.get("data", [])
    print(f"OK  Jaeger services: {services}")

    required_services = ["ai-code-review-api", "ai-code-review-worker"]
    for service in required_services:
        if service not in services:
            raise TracingValidationError(
                f"Expected Jaeger service '{service}' not registered"
            )

    time.sleep(TRACE_WAIT_SECONDS)

    worker_traces = _jaeger_traces("ai-code-review-worker")
    if not worker_traces:
        raise TracingValidationError("No worker traces found in Jaeger")

    span_names: set[str] = set()
    for trace in worker_traces:
        for span in trace.get("spans", []):
            span_names.add(span.get("operationName", ""))

    expected_spans = {
        "review.job",
        "review.pipeline",
        "review.pipeline.diff_processing",
    }
    missing = expected_spans - span_names
    if missing:
        raise TracingValidationError(
            f"Worker traces missing expected spans {sorted(missing)}; "
            f"found {sorted(span_names)}"
        )

    api_traces = _jaeger_traces("ai-code-review-api")
    if not api_traces:
        raise TracingValidationError("No API traces found in Jaeger")

    print("OK  Jaeger contains API and worker traces with review pipeline spans")


def main() -> int:
    print(f"Tracing validation against API={API_BASE_URL} Jaeger={JAEGER_QUERY_URL}")
    try:
        _request("GET", f"{API_BASE_URL}/health")
        print("OK  API health")
    except urllib.error.URLError as exc:
        raise TracingValidationError(f"API unavailable: {exc}") from exc

    job_id = trigger_review()
    print(f"OK  Review queued (job_id={job_id})")
    wait_for_job(job_id)
    assert_traces_present()
    print("\nALL TRACING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TracingValidationError as exc:
        print(f"\nTRACING VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
