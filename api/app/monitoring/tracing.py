"""OpenTelemetry tracing setup and helpers for the API service."""
from __future__ import annotations

import logging
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

from app.config import settings

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_instrumented = False


def is_tracing_enabled() -> bool:
    return settings.OTEL_ENABLED


def setup_tracing(
    *,
    exporter: SpanExporter | None = None,
    enabled: bool | None = None,
) -> TracerProvider | None:
    """Configure the global tracer provider and OTLP export."""
    global _provider

    is_enabled = settings.OTEL_ENABLED if enabled is None else enabled
    if not is_enabled:
        logger.info("OpenTelemetry tracing disabled (OTEL_ENABLED=false)")
        return None

    service = settings.OTEL_SERVICE_NAME or settings.service_name
    resource = Resource.create({"service.name": service})
    _provider = TracerProvider(resource=resource)

    if exporter is None:
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
        )
        _provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    else:
        _provider.add_span_processor(SimpleSpanProcessor(exporter))

    trace.set_tracer_provider(_provider)
    logger.info(
        "OpenTelemetry tracing enabled",
        extra={
            "service": service,
            "endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        },
    )
    return _provider


def instrument_app(app: object) -> None:
    """Auto-instrument FastAPI and the Celery producer client."""
    global _instrumented

    if not is_tracing_enabled() or _instrumented:
        return

    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    CeleryInstrumentor().instrument()
    _instrumented = True
    logger.info("OpenTelemetry auto-instrumentation enabled for FastAPI and Celery")


def get_tracer(name: str | None = None) -> trace.Tracer:
    service = settings.OTEL_SERVICE_NAME or settings.service_name
    return trace.get_tracer(name or service)


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
