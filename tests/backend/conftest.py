"""Backend test environment: a synthetic M7 cost table built offline into a temp directory.

The published demo table under ``artifacts/`` is ignored by Git, so every test run builds
its own with the M7 builder (deterministic, about half a second).
"""
import pytest

from claim_cmev.costs.reference import active_table_version
from claim_cmev.costs.reference.build import main as build_cost_table


@pytest.fixture(scope="session")
def cost_tables(tmp_path_factory):
    root = tmp_path_factory.mktemp("cost_tables")
    assert build_cost_table(["--seed", "20260924", "--out", str(root), "--promote"]) == 0
    assert active_table_version(root)
    return root


@pytest.fixture(autouse=True)
def runtime_env(monkeypatch, cost_tables):
    monkeypatch.setenv("CMEV_COST_TABLE_PATH", str(cost_tables))
    monkeypatch.delenv("CMEV_ACTIVE_COST_TABLE_VERSION", raising=False)
    monkeypatch.setenv("CMEV_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CMEV_FIXTURE_MODE", "true")
    monkeypatch.setenv("CMEV_DEMO_PASSWORD", "Demo2026!")
    monkeypatch.setenv("CMEV_PROFILE", "lean")
