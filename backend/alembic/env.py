"""Alembic environment wired to the SPIDEY SQLAlchemy metadata.

The database URL comes from the app settings (``DATABASE_URL`` env var or
``backend/.env``), not from ``alembic.ini``. The conditional pgvector
``Vector`` type on ``document_chunks.embedding`` is handled in
``app.models``: it resolves to ``pgvector.sqlalchemy.Vector`` when
``VECTOR_BACKEND=pgvector`` and to ``JSON`` otherwise, so this module stays
importable under both PostgreSQL and sqlite.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Ensure `import app...` resolves when alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point alembic at the app's database URL (env var wins over .env).
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
