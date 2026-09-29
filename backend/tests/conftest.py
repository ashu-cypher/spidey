"""Phase 2 — test bootstrap.

Runs the whole suite against a throwaway sqlite database in /tmp (env var
only, set BEFORE any ``app`` module is imported, so the lazily-created
engine picks it up). ``VECTOR_BACKEND=local`` keeps the ``embedding`` column
as JSON, which sqlite can compile.
"""
import os

_SQLITE_PATH = "/tmp/spidey_test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_SQLITE_PATH}"
os.environ["VECTOR_BACKEND"] = "local"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fresh_test_db():
    from app.database import init_db

    if os.path.exists(_SQLITE_PATH):
        os.remove(_SQLITE_PATH)
    init_db()
    yield
    if os.path.exists(_SQLITE_PATH):
        os.remove(_SQLITE_PATH)
