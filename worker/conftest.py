import os
import sys

# Ensure the `app` package is importable when running tests from any location.
sys.path.insert(0, os.path.dirname(__file__))

# Use an in-memory SQLite database for tests so no real PostgreSQL is required.
# Must be set before any `app` module imports the engine.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database():
    """Create the worker's tables (review_jobs) once for the test session."""
    import app.reviews.analytics_store  # noqa: F401 - registers analytics ORM models
    import app.reviews.job_store  # noqa: F401 - registers the ORM model
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
