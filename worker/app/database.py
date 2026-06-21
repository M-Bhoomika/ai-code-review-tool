"""Worker database access.

Connects the worker to the same PostgreSQL database the API uses (via
``DATABASE_URL``) so review job state is shared across services. The
``review_jobs`` table is owned/created by the API's Alembic migrations; the
worker only reads and writes rows.

SQLite is supported for tests (single shared in-memory connection).
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/code_review"


def normalize_database_url(url: str) -> str:
    """Rewrite the PostgreSQL scheme to use psycopg (v3); leave others alone."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = normalize_database_url(
    os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
)


class Base(DeclarativeBase):
    """Declarative base for worker-side ORM models."""


def _create_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, pool_pre_ping=True, future=True)


engine: Engine = _create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)
