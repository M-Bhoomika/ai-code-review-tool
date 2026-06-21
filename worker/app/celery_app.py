import os

from celery import Celery
from celery.signals import worker_process_init, worker_shutdown

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "ai_code_review_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

# Ensure tasks are registered when the worker starts.
celery_app.autodiscover_tasks(["app"])

import app.tasks  # noqa: E402,F401

from app.monitoring.review_metrics import start_metrics_server
from app.monitoring.tracing import instrument_celery, setup_tracing, shutdown_tracing


def _metrics_enabled() -> bool:
    return os.getenv("WORKER_METRICS_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@worker_process_init.connect
def _init_worker_observability(**_kwargs: object) -> None:
    setup_tracing()
    instrument_celery()
    if _metrics_enabled():
        start_metrics_server(int(os.getenv("WORKER_METRICS_PORT", "9100")))


@worker_shutdown.connect
def _shutdown_worker_observability(**_kwargs: object) -> None:
    shutdown_tracing()
