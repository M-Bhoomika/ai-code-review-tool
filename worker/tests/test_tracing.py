"""Unit tests for worker OpenTelemetry tracing."""
from __future__ import annotations

import pytest
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


def test_pipeline_span_names():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(exporter=exporter)

    with tracing.span("review.pipeline", repository="octocat/hello", pull_number=42):
        with tracing.span("review.pipeline.diff_processing"):
            pass
        with tracing.span("review.pipeline.analysis"):
            pass

    spans = exporter.get_finished_spans()
    names = {span.name for span in spans}
    assert "review.pipeline" in names
    assert "review.pipeline.diff_processing" in names
    assert "review.pipeline.analysis" in names


def test_review_job_span():
    exporter = InMemorySpanExporter()
    tracing.setup_tracing(exporter=exporter)

    with tracing.span(
        "review.job",
        job_id="abc-123",
        repository="octocat/hello",
        pull_number=7,
    ):
        pass

    spans = exporter.get_finished_spans()
    job_span = next(span for span in spans if span.name == "review.job")
    assert job_span.attributes["job_id"] == "abc-123"
    assert job_span.attributes["repository"] == "octocat/hello"


def test_tracing_disabled():
    provider = tracing.setup_tracing(enabled=False)
    assert provider is None
