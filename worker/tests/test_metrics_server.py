"""Tests for the worker Prometheus metrics HTTP exporter."""
from __future__ import annotations

from unittest.mock import patch

from app.monitoring import review_metrics


def test_start_metrics_server_binds_port():
    with patch("app.monitoring.review_metrics.start_http_server") as mock_server:
        review_metrics.start_metrics_server(9100)
        mock_server.assert_called_once_with(9100)


def test_metrics_endpoint_serves_registered_metrics():
    from prometheus_client import REGISTRY

    from app.monitoring.review_metrics import review_jobs_total

    before = REGISTRY.get_sample_value("review_jobs_total") or 0.0
    review_metrics.record_review_started()
    after = REGISTRY.get_sample_value("review_jobs_total")
    assert after == before + 1
