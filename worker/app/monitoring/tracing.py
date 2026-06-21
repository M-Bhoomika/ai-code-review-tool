"""OpenTelemetry tracing setup and helpers for the worker service."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "ai-code-review-worker"
_provider: TracerProvider | None = None
_instrumented = False


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_tracing_enabled() -> bool:
    return _parse_bool(os.getenv("OTEL_ENABLED"), default=True)


def otlp_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")


def service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)


def setup_tracing(
    *,
    exporter: SpanExporter | None = None,
    enabled: bool | None = None,
) -> TracerProvider | None:
    """Configure the global tracer provider and OTLP export."""
    global _provider

    is_enabled = is_tracing_enabled() if enabled is None else enabled
    if not is_enabled:
        logger.info("OpenTelemetry tracing disabled (OTEL_ENABLED=false)")
        return None

    name = service_name()
    resource = Resource.create({"service.name": name})
    _provider = TracerProvider(resource=resource)

    if exporter is None:
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint(),
            insecure=_parse_bool(os.getenv("OTEL_EXPORTER_OTLP_INSECURE"), default=True),
        )
        _provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    else:
        _provider.add_span_processor(SimpleSpanProcessor(exporter))

    trace.set_tracer_provider(_provider)
    logger.info(
        "OpenTelemetry tracing enabled",
        extra={"service": name, "endpoint": otlp_endpoint()},
    )
    return _provider


def instrument_celery() -> None:
    """Auto-instrument Celery task execution."""
    global _instrumented

    if not is_tracing_enabled() or _instrumented:
        return

    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    CeleryInstrumentor().instrument()
    _instrumented = True
    logger.info("OpenTelemetry Celery auto-instrumentation enabled")


def get_tracer(name: str | None = None) -> trace.Tracer:
    return trace.get_tracer(name or service_name())


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Create a child span with optional string attributes."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, str(value))
        yield current


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    global _provider, _instrumented

    if _provider is not None:
        _provider.shutdown()
    _provider = None
    _instrumented = False
