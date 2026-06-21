import os
import sys

# Ensure the `app` package is importable when running tests from any location.
sys.path.insert(0, os.path.dirname(__file__))

# Use an in-memory SQLite database for tests so no real PostgreSQL is required.
# Must be set before any `app` module imports the settings/engine.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database():
    """Create all tables once for the test session."""
    import app.models  # noqa: F401 - registers models on Base.metadata
    from app.database.base import Base
    from app.database.session import engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
