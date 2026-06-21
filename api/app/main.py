import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config import settings
from app.database.session import engine
from app.graphql.router import graphql_router
from app.health import router as health_router
from app.monitoring.tracing import instrument_app, setup_tracing, shutdown_tracing
from app.routes.reviews import router as reviews_router
from app.webhooks import router as webhook_router

logger = logging.getLogger(settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Verify database connectivity on startup without blocking the app."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("Database connectivity verified")
    except Exception as exc:  # noqa: BLE001 - log and continue degraded
        logger.warning(
            "Database unavailable at startup; continuing in degraded mode: %s",
            exc,
        )
    yield
    shutdown_tracing()


setup_tracing()

app = FastAPI(
    title="AI Code Review API",
    version="0.1.0",
    description="Backend API for the AI-powered code review tool.",
    lifespan=lifespan,
)

# Allow the local frontend (and other clients) to call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["health"])
app.include_router(webhook_router, tags=["webhooks"])
app.include_router(reviews_router, tags=["reviews"])
app.include_router(graphql_router, tags=["graphql"])

instrument_app(app)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AI Code Review API"}


@app.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus metrics for scraping."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
