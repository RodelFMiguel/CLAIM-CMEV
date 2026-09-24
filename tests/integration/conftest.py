"""Shared fixtures for the runtime integration tests (SQLite + the in-memory transport).

These exercise the real consumer runtime, orchestrator, fixture producers and the M8
consolidator with a synthetic M7 table built into a temp directory. The in-memory broker
reproduces partitioning, per-group offsets and redelivery; it is not evidence that the
Kafka or PostgreSQL integration passed.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from claim_cmev.costs.reference.build import main as build_cost_table
from claim_cmev.orchestration.services import local_pipeline
from claim_cmev.runtime import Database
from pipeline_helpers import Clock


@pytest.fixture(scope="session")
def cost_tables(tmp_path_factory):
    root = tmp_path_factory.mktemp("cost_tables")
    assert build_cost_table(["--seed", "20260924", "--out", str(root), "--promote"]) == 0
    return root


@pytest.fixture(autouse=True)
def runtime_env(monkeypatch, cost_tables):
    monkeypatch.setenv("CMEV_COST_TABLE_PATH", str(cost_tables))
    monkeypatch.delenv("CMEV_ACTIVE_COST_TABLE_VERSION", raising=False)
    monkeypatch.setenv("CMEV_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CMEV_FIXTURE_MODE", "true")
    monkeypatch.setenv("CMEV_DEMO_PASSWORD", "Demo2026!")
    monkeypatch.setenv("CMEV_PROFILE", "lean")


@pytest.fixture
def database(tmp_path):
    """SQLite by default; set ``CMEV_TEST_POSTGRES_URL`` to run against a disposable PostgreSQL."""
    url = os.getenv("CMEV_TEST_POSTGRES_URL")
    if url:
        db = Database(url)
        with db.engine.begin() as connection:  # a clean database for every test; never point this at real data
            for schema in ("ops", "pipeline", "assessment"):
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS baseline_records, alembic_version"))
    else:
        db = Database("sqlite:///" + str(tmp_path / "runtime.db"))
    db.initialize()
    yield db
    db.engine.dispose()


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def pipeline(database, sleeps):
    return local_pipeline(database, sleep=sleeps.append, clock=Clock())
