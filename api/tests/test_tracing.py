"""Unit tests for OpenTelemetry tracing setup."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from app.monitoring import tracing


def _reset_otel_globals() -> None:
    if trace._TRACER_PROVIDER is not None:
        trace._TRACER_PROVIDER.shutdown()
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    tracing.shutdown_tracing()


@pytest.fixture(autouse=True)
def reset_tracing():
    _reset_otel_globals()
    yield
    _reset_otel_globals()


def test_fastapi_request_creates_http_span():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(exporter=exporter)

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    tracing.instrument_app(app)
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert spans
    assert any("health" in span.name for span in spans)


def test_manual_span_records_attributes():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(exporter=exporter)

    with tracing.span("review.test", repository="octocat/hello", pull_number=42):
        pass

    spans = exporter.get_finished_spans()
    assert any(span.name == "review.test" for span in spans)
    test_span = next(span for span in spans if span.name == "review.test")
    assert test_span.attributes["repository"] == "octocat/hello"
    assert test_span.attributes["pull_number"] == "42"


def test_tracing_disabled():
    provider = tracing.setup_tracing(enabled=False)
    assert provider is None
