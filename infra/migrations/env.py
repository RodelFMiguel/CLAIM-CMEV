"""Alembic environment for CLAIM-CMEV (technical specification section 8.5).

Forward-only. ``claim_cmev.persistence.migrations.upgrade`` runs this under a PostgreSQL
advisory lock and passes its connection in ``config.attributes["connection"]``. Run by
hand with ``CMEV_DATABASE_URL`` set. SQLite (tests, ``--local``) maps the named schemas
onto its default schema.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from claim_cmev.persistence.tables import SQLITE_SCHEMA_MAP

config = context.config


def _run(connection) -> None:
    if connection.dialect.name == "sqlite":
        connection = connection.execution_options(schema_translate_map=SQLITE_SCHEMA_MAP)
    context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = os.environ.get("CMEV_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise SystemExit("CMEV_DATABASE_URL is required to run migrations")
    engine = create_engine(url)
    with engine.begin() as connection:
        _run(connection)


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run against a database")
run_online()
