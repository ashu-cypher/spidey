"""Phase 2 — database layer: lazy engine, session factory, schema bootstrap.

The engine is created lazily (on first ``get_session()`` / ``init_db()`` call),
never at import time, so tests can point ``DATABASE_URL`` at a scratch database
via the environment before any ``app`` module is imported.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, User

__all__ = ["get_engine", "SessionLocal", "get_session", "init_db"]

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def _build_engine() -> Engine:
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set — the database layer needs it. "
            "Set it in backend/.env or the environment."
        )
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine() -> Engine:
    """Return the shared engine, creating it on first use."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = _build_engine()
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    assert _SessionFactory is not None
    return _SessionFactory()


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session with commit-on-success / rollback-on-error semantics."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables (dev convenience bootstrap; migrations are canonical)."""
    engine = get_engine()
    if engine.url.get_backend_name() == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            # Extension may already exist or need superuser; schema creation
            # proceeds anyway when the type is unused by the DDL.
            pass
    Base.metadata.create_all(engine)
    # Seed the default local user so FK references (user_id="local") succeed.
    with get_session() as session:
        if session.get(User, "local") is None:
            session.add(User(id="local", name="local"))
