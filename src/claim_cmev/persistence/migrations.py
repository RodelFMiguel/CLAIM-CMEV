"""Alembic upgrade and head checks (technical specification section 8.5).

``cmev-api`` runs ``upgrade`` at start under a PostgreSQL advisory lock, so concurrent
replicas never race. Every other service calls ``check_head`` and refuses readiness when
the database head differs from ``EXPECTED_HEAD``: a partially migrated cluster must not
process claims.
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

EXPECTED_HEAD = "0001_runtime_schema"
MIGRATION_LOCK = 72_011_024
"""Arbitrary constant key for ``pg_advisory_xact_lock``; one per project."""
IMAGE_MIGRATIONS = Path("/srv/infra/migrations")


class SchemaHeadMismatch(RuntimeError):
    """The database is not at the head this code was built against."""

    reason_code = "schema_head_mismatch"


def migrations_dir() -> Path:
    """``$CMEV_MIGRATIONS_DIR``, the repository's ``infra/migrations`` or the image copy."""
    configured = os.getenv("CMEV_MIGRATIONS_DIR")
    if configured:
        return Path(configured)
    repository = Path(__file__).resolve().parents[3] / "infra" / "migrations"
    return repository if (repository / "env.py").is_file() else IMAGE_MIGRATIONS


def upgrade(engine: Engine) -> str:
    """Upgrade to head inside one transaction holding the migration advisory lock."""
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return current_head(engine) or ""


def current_head(engine: Engine) -> str | None:
    with engine.connect() as connection:
        try:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:  # noqa: BLE001 - no version table means an unmigrated database
            return None


def check_head(engine: Engine) -> str:
    """Raise ``SchemaHeadMismatch`` unless the database is exactly at ``EXPECTED_HEAD``."""
    head = current_head(engine)
    if head != EXPECTED_HEAD:
        raise SchemaHeadMismatch(f"database head {head!r} differs from expected {EXPECTED_HEAD!r}")
    return head


__all__ = ["EXPECTED_HEAD", "SchemaHeadMismatch", "check_head", "current_head", "migrations_dir", "upgrade"]
