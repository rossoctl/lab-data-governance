"""Alembic environment.

Per ADR-0002 we use Alembic without the SQLAlchemy ORM — there is no
``target_metadata``, and migrations are hand-written SQL via ``op.execute``.

The Postgres URL comes from the ``DATABASE_URL`` environment variable. We
override whatever ``sqlalchemy.url`` ``alembic.ini`` may carry so deployments
configure the URL via env (12-factor) rather than a config file.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# No SQLAlchemy ORM => no metadata. Migrations are raw op.execute(...) calls.
target_metadata = None


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; the migrate CLI requires a Postgres URL "
            "(e.g. postgresql://user:pass@host:5432/dbname)."
        )
    # psycopg accepts both ``postgres://`` and ``postgresql://`` and the v1
    # k8s manifests construct the DSN as ``postgres://...``. SQLAlchemy
    # (used by Alembic here) only knows ``postgresql://``, so canonicalise
    # the historical ``postgres://`` alias up front.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # SQLAlchemy's default ``postgresql://`` dialect dispatches to psycopg2,
    # which we deliberately do not depend on (ADR-0005 pins psycopg 3). Force
    # the psycopg 3 driver so SQLAlchemy uses the same lib as the rest of
    # the receiver. Existing ``postgresql+...`` URLs are left untouched so
    # operators can override (e.g. asyncpg in a future test).
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emit SQL to stdout)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    config.set_main_option("sqlalchemy.url", _resolve_url())
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
