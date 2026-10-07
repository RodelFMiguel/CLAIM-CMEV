"""The API when the real M1 worker owns the parts stage (``CMEV_PARTS_PRODUCER=real``).

No model runs here: these check what the API reports and that the fixture seed claims,
which have no photo bytes, still get their fixture assessments at start-up.
"""
from fastapi.testclient import TestClient
import pytest

from claim_cmev.api.main import create_app
from claim_cmev.contracts.fixtures import VERSIONS as FIXTURE_VERSIONS
from claim_cmev.vision.parts.config import load_parts_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    app = create_app("sqlite:///" + str(tmp_path / "test.db"), tmp_path / "evidence")
    with TestClient(app) as started:
        login = started.post("/api/v1/auth/login",
                             json={"email": "surveyor@claim-cmev.demo", "password": "Demo2026!"})
        assert login.status_code == 200
        yield started


def test_version_endpoint_reports_the_versions_the_orchestrator_pins_for_parts(client):
    stage_versions = client.get("/api/v1/version").json()["stage_versions"]
    assert stage_versions["parts"] == load_parts_config().stage_versions("0.2.0")
    assert stage_versions["damage"] == FIXTURE_VERSIONS["damage"]


def test_seed_claims_still_get_their_fixture_assessments(client):
    """Seeds are fixture demonstrations without photo bytes; the real parts worker cannot serve them."""
    seeded = next(c for c in client.get("/api/v1/claims").json()["items"] if c["reference"] == "CLM-24019")
    assessment = client.get(f"/api/v1/claims/{seeded['claim_id']}/assessments/1")
    assert assessment.status_code == 200
