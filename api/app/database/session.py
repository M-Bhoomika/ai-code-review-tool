from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


def normalize_database_url(url: str) -> str:
    """Ensure the URL uses the psycopg (v3) driver for PostgreSQL.

    SQLAlchemy defaults to psycopg2 for the bare ``postgresql://`` scheme, but
    this project pins ``psycopg[binary]`` (psycopg 3), so we rewrite the scheme
    accordingly while leaving other dialects (e.g. SQLite) untouched.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = normalize_database_url(settings.DATABASE_URL)


def _create_engine(url: str) -> Engine:
    # SQLite (used in tests) does not accept the connection-pool tuning options
    # and needs a shared, single connection for in-memory databases.
    if url.startswith("sqlite"):
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
        future=True,
    )


engine: Engine = _create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
