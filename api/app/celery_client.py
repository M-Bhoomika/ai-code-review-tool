from celery import Celery

from app.config import settings

# The API does not execute tasks; it only enqueues them. This Celery instance
# shares the broker/backend with the worker so dispatched tasks are routed by
# name to the worker process that owns the real implementation.
celery_client = Celery(
    "ai_code_review_api",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_client.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


@celery_client.task(name="review_pull_request")
def review_pull_request(
    repository: str, pr_number: int, installation_id: int
) -> None:
    """Producer-side stub for the ``review_pull_request`` task.

    The body is never run in the API process; the worker registers a task with
    the same name and performs the actual work. This declaration exists so the
    API can dispatch via ``review_pull_request.delay(...)``.
    """
    raise NotImplementedError(
        "review_pull_request is executed by the worker service"
    )
