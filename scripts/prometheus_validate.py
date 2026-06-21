#!/usr/bin/env python3
"""Validate Prometheus scrapes API and worker review metrics.

Requires the platform stack with Prometheus running:

  docker compose up --build -d
  python scripts/prometheus_validate.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_METRICS_URL = os.getenv("API_METRICS_URL", "http://localhost:8000/metrics")
WORKER_METRICS_URL = os.getenv("WORKER_METRICS_URL", "http://localhost:9100/metrics")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
SCRAPE_WAIT_SECONDS = int(os.getenv("PROMETHEUS_SCRAPE_WAIT_SECONDS", "20"))

REQUIRED_METRICS = (
    "review_jobs_total",
    "review_jobs_success_total",
    "review_jobs_failed_total",
    "review_comments_published_total",
    "review_pipeline_duration_seconds",
)


class PrometheusValidationError(RuntimeError):
    """Raised when Prometheus validation fails."""


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def _prometheus_query(query: str) -> dict:
    url = (
        f"{PROMETHEUS_URL}/api/v1/query?"
        + urllib.parse.urlencode({"query": query})
    )
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def assert_direct_metrics_endpoints() -> None:
    api_body = _fetch_text(API_METRICS_URL)
    worker_body = _fetch_text(WORKER_METRICS_URL)

    for metric in REQUIRED_METRICS:
        if metric not in api_body:
            raise PrometheusValidationError(
                f"API metrics missing metric definition: {metric}"
            )
        if metric not in worker_body:
            raise PrometheusValidationError(
                f"Worker metrics missing metric definition: {metric}"
            )

    print(f"OK  API metrics endpoint exposes required metrics ({API_METRICS_URL})")
    print(
        "OK  Worker metrics endpoint exposes required metrics "
        f"({WORKER_METRICS_URL})"
    )


def assert_prometheus_targets() -> None:
    url = f"{PROMETHEUS_URL}/api/v1/targets"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    active = payload.get("data", {}).get("activeTargets", [])
    jobs = {
        target.get("labels", {}).get("job")
        for target in active
        if target.get("health") == "up"
    }
    print(f"OK  Prometheus active targets (up): {sorted(j for j in jobs if j)}")

    for required in ("api", "worker"):
        if required not in jobs:
            raise PrometheusValidationError(
                f"Prometheus target job '{required}' is not healthy/up"
            )


def assert_prometheus_metric_series() -> None:
    for job in ("api", "worker"):
        result = _prometheus_query(f'up{{job="{job}"}}')
        series = result.get("data", {}).get("result", [])
        if not series or float(series[0]["value"][1]) != 1.0:
            raise PrometheusValidationError(f"Prometheus query up{{job='{job}'}} failed")

    worker_metric = _prometheus_query('review_jobs_total{job="worker"}')
    if not worker_metric.get("data", {}).get("result"):
        raise PrometheusValidationError(
            "Prometheus has no review_jobs_total series for job=worker"
        )

    api_metric = _prometheus_query('review_jobs_total{job="api"}')
    if not api_metric.get("data", {}).get("result"):
        raise PrometheusValidationError(
            "Prometheus has no review_jobs_total series for job=api"
        )

    print("OK  Prometheus stores review_jobs_total for api and worker jobs")


def main() -> int:
    print(
        "Prometheus validation against "
        f"API={API_METRICS_URL} Worker={WORKER_METRICS_URL} Prometheus={PROMETHEUS_URL}"
    )
    try:
        assert_direct_metrics_endpoints()
        time.sleep(SCRAPE_WAIT_SECONDS)
        assert_prometheus_targets()
        assert_prometheus_metric_series()
    except urllib.error.URLError as exc:
        raise PrometheusValidationError(f"HTTP request failed: {exc}") from exc

    print("\nALL PROMETHEUS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PrometheusValidationError as exc:
        print(f"\nPROMETHEUS VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
