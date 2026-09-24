"""The first implementation milestone end to end, profile parity, readiness refusal and schema constraints.

Upload -> input revision -> fixture stages -> genuine M8 rules with a pinned synthetic
table -> persisted assessment -> API view with results and reasons -> mark decision ->
reassessment with recorded reuse lineage and no stage rerun. SQLite, local evidence
storage and the in-memory transport; not Kafka, PostgreSQL or MinIO.
"""
from __future__ import annotations

from io import BytesIO
import json
import shutil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from claim_cmev.api.main import create_app
from claim_cmev.costs.reference import CostTableError, active_table_version
from claim_cmev.orchestration.consolidation import Consolidator
from claim_cmev.orchestration.plan import VersionBundle
from claim_cmev.orchestration.services import LocalPipeline, RuntimeSettings, build_runtimes
from claim_cmev.persistence.migrations import SchemaHeadMismatch, check_head
from claim_cmev.persistence.tables import assessments, jobs
from claim_cmev.runtime import Database, Record, get
from claim_cmev.worker import local_tick, startup_checks
from pipeline_helpers import ALL_STAGES, Clock, make_claim

CLAIM = "01K6F1XTVRE00000000000B002"


def png(color="white"):
    data = BytesIO()
    Image.new("RGB", (800, 600), color).save(data, format="PNG")
    return data.getvalue()


def post(client, path, body, key=None):
    return client.post("/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


@pytest.fixture
def api(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "api.db"), tmp_path / "evidence")
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/login", json={"email": "surveyor@claim-cmev.demo",
                                                       "password": "Demo2026!"}).status_code == 200
        yield client, app.state.database


def test_upload_to_reassessment_milestone(api):
    client, database = api
    created = post(client, "/claims", {"reference": "E2E-1", "vehicle_make": "Toyota", "vehicle_model": "Corolla",
                                       "vehicle_year": 2019, "vehicle_class": "sedan_standard"}).json()
    cid = created["claim_id"]
    with database.session.begin() as db:  # pin the scenario so the pending mark is known in advance
        row = db.get(Record, "claim:" + cid)
        row.data = {**row.data, "fixture_scenario": "pending_price_change"}
    photos = [client.post(f"/api/v1/claims/{cid}/files", data={"role": "photograph"},
                          files={"files": (f"p{i}.png", png(c), "image/png")},
                          headers={"Idempotency-Key": str(uuid4())}).json()["files"][0]["file_id"]
              for i, c in enumerate(("white", "grey"))]
    page = client.post(f"/api/v1/claims/{cid}/files", data={"role": "estimate_page"},
                       files={"files": ("estimate.png", png("ivory"), "image/png")},
                       headers={"Idempotency-Key": str(uuid4())}).json()["files"][0]["file_id"]
    committed = post(client, f"/claims/{cid}/input-revisions", {"file_ids": [*photos, page]}, "commit-1")
    assert committed.status_code == 202 and committed.json()["state"] == "queued"
    processing = client.get(f"/api/v1/claims/{cid}/processing").json()
    assert processing["dispatch_pending"] and processing["state"] == "processing"
    assert processing["processing_state"] == "queued"
    assert {s["state"] for s in processing["stages"]} == {"pending"}

    local_tick(database)
    processing = client.get(f"/api/v1/claims/{cid}/processing").json()
    assert {s["stage"]: s["state"] for s in processing["stages"]} == {s: "done" for s in ALL_STAGES}
    assert processing["consolidate"]["state"] == "succeeded" and not processing["dispatch_pending"]
    assert not processing["incomplete"] and processing["branches"] == {"image": "complete", "document": "complete"}
    claim = client.get(f"/api/v1/claims/{cid}").json()
    assert claim["status"] == "in_review" and claim["assessment_revision"] == 1

    view = client.get(f"/api/v1/claims/{cid}/assessments/1").json()
    assert view["provenance"]["source_kind"] == "fixture" and view["fixture_notice"]
    assert view["state"] == "ready" and view["cost_table_version"] == view["versions"]["cost_table"]
    assert view["rules_config_version"] == "m8-rules/0.1.0" and view["reuse_lineage"] == []
    assert len(view["findings"]) == len(view["line_items"]) == 3
    first = view["line_items"][0]
    assert first["printed_amount"] == "1150.00" and first["effective_amount"] is None
    assert first["mark_state"] == "pending" and "mark_pending" in first["reason_codes"]
    assert first["overall_result"] == "insufficient_evidence" and first["reason"]
    assert all(code in view["reason_texts"] for item in view["line_items"] for code in item["reason_codes"])
    assert not view["finalize_preconditions"]["can_finalize"]
    mark = next(m for m in view["marks"] if m["state"] == "pending")
    assert client.get(f"/api/v1/claims/{cid}/evidence/{photos[0]}").content == png("white")

    with database.session() as db:
        stage_jobs = db.execute(select(func.count()).select_from(jobs).where(jobs.c.claim_id == cid)).scalar()
        original = db.execute(select(assessments.c.body).where(assessments.c.claim_id == cid)).scalar()
    decision = post(client, f"/claims/{cid}/assessments/1/marks/{mark['mark_id']}/decision",
                    {"expected_input_revision": 1, "expected_review_revision": 0, "decision": "confirm",
                     "amount": "1000.10"}, "mark-1")
    assert decision.status_code == 202
    assert decision.json()["input_revision"] == 2 and sorted(decision.json()["reused_stages"]) == ALL_STAGES
    assert post(client, f"/claims/{cid}/assessments/1/marks/{mark['mark_id']}/decision",
                {"expected_input_revision": 1, "expected_review_revision": 0, "decision": "confirm",
                 "amount": "1000.10"}, "mark-1").json() == decision.json()  # idempotent replay
    local_tick(database)
    claim = client.get(f"/api/v1/claims/{cid}").json()
    assert claim["input_revision"] == 2 and claim["assessment_revision"] == 2 and claim["review_revision"] == 1
    new = client.get(f"/api/v1/claims/{cid}/assessments/2").json()
    assert new["trigger"] == "reassessment" and new["review_revision"] == 1
    assert sorted(entry["stage"] for entry in new["reuse_lineage"]) == ALL_STAGES
    assert all(entry["source_input_revision"] == 1 for entry in new["reuse_lineage"])
    assert [a["source"] for a in new["mark_actions_applied"]] == ["surveyor"]
    assert new["line_items"][0]["effective_amount"] == "1000.10"
    assert new["line_items"][0]["printed_amount"] == "1150.00"  # the printed amount is never overwritten
    assert new["finalize_preconditions"]["can_finalize"]
    with database.session() as db:
        tasks = sorted(db.execute(select(jobs.c.task).where(jobs.c.claim_id == cid, jobs.c.input_revision == 2))
                       .scalars())
        assert db.execute(select(func.count()).select_from(jobs).where(jobs.c.claim_id == cid)).scalar() == stage_jobs + 2
        assert db.execute(select(assessments.c.body).where(assessments.c.claim_id == cid,
                                                           assessments.c.assessment_revision == 1)).scalar() == original
    assert tasks == ["consolidate", "intake"]  # a mark decision reruns no stage

    succeeded = client.get(f"/api/v1/claims/{cid}/jobs?input_revision=1").json()["jobs"][1]["job_key"]
    assert client.post(f"/api/v1/claims/{cid}/jobs/{succeeded}/retry",
                       headers={"Idempotency-Key": "r1"}).status_code == 409
    assert client.post(f"/api/v1/claims/{cid}/jobs/{cid}:9:page_read:x:00000000/retry",
                       headers={"Idempotency-Key": "r2"}).status_code == 404
    assert client.get("/api/v1/admin/dead-letters").json() == {"items": []}
    items = client.get(f"/api/v1/claims/{cid}/assessments").json()["items"]
    assert [(a["assessment_revision"], a["is_current"]) for a in items] == [(1, False), (2, True)]


def test_seed_claims_come_from_the_pipeline(api):
    client, database = api
    claims = {c["reference"]: c for c in client.get("/api/v1/claims").json()["items"]}
    assert claims["CLM-24019"]["status"] == "in_review"
    assert claims["CLM-24018"]["status"] == "ready_to_print"
    assert claims["CLM-24021"]["status"] == "awaiting_upload"
    assert claims["CLM-24020"]["status"] == "processing"  # queued for the worker
    local_tick(database)
    seeded = client.get(f"/api/v1/claims/{claims['CLM-24020']['claim_id']}/assessments/1").json()
    results = sorted((i["row_state"], i["overall_result"]) for i in seeded["line_items"])
    assert ("excluded", "not_evaluated") in results  # a confirmed exclusion is a row state, never ok
    with database.session() as db:
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 3
        assert all(b["provenance"]["source_kind"] == "fixture" for b in db.execute(select(assessments.c.body)).scalars())


def test_lean_and_full_profiles_produce_identical_findings(tmp_path, cost_tables):
    bodies = {}
    for profile, roles in (("lean", ["combined"]), ("full", ["orchestrator", "producers", "consolidator"])):
        database = Database("sqlite:///" + str(tmp_path / f"{profile}.db"))
        database.initialize()
        consolidator = Consolidator(cost_table_root=cost_tables)
        runtimes = [rt for role in roles for rt in build_runtimes(
            database.session, role, versions=VersionBundle.fixture(), consolidator=consolidator, profile=profile,
            clock=Clock(), sleep=lambda _s: None)]
        pipeline = LocalPipeline(database.session, runtimes, clock=Clock())
        make_claim(database, "exclusion_and_supported", cost_tables, claim_id=CLAIM, clock=Clock())
        pipeline.drain()
        with database.session() as db:
            bodies[profile] = db.execute(select(assessments.c.body)).scalar_one()
            bodies[profile + "_keys"] = sorted(db.execute(select(jobs.c.job_key)).scalars())
    lean, full = bodies["lean"], bodies["full"]

    def untimed(findings):  # created_at is when the consolidator ran, not part of a finding's content
        return [{k: v for k, v in f.items() if k != "created_at"} for f in findings]
    assert untimed(lean["findings"]) == untimed(full["findings"]) and lean["findings"]
    assert [f["content_hash"] for f in lean["findings"]] == [f["content_hash"] for f in full["findings"]]
    assert lean["pinned_versions"] == full["pinned_versions"]
    assert lean["possible_additions"] == full["possible_additions"]
    assert bodies["lean_keys"] == bodies["full_keys"]
    assert (lean["provenance"]["runtime_profile"], full["provenance"]["runtime_profile"]) == ("lean", "full")


def test_unloadable_cost_table_refuses_readiness_and_never_fabricates(tmp_path, cost_tables, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CostTableError):
        Consolidator(cost_table_root=empty).readiness()
    unmigrated = Database("sqlite:///" + str(tmp_path / "fresh.db"))
    with pytest.raises(SchemaHeadMismatch):
        startup_checks(unmigrated, RuntimeSettings.from_env(), "consolidator", Consolidator(cost_table_root=cost_tables))
    tampered = tmp_path / "tampered"
    shutil.copytree(cost_tables, tampered)
    version = active_table_version(tampered)
    ranges = tampered / version / "ranges.json"
    ranges.chmod(0o644)
    ranges.write_text(json.dumps(json.loads(ranges.read_text())[:-1]))
    database = Database("sqlite:///" + str(tmp_path / "tampered.db"))
    database.initialize()
    with pytest.raises(CostTableError):
        startup_checks(database, RuntimeSettings.from_env(), "consolidator", Consolidator(cost_table_root=tampered))
    consolidator = Consolidator(cost_table_root=tampered)
    pipeline = LocalPipeline(database.session, build_runtimes(
        database.session, "combined", versions=VersionBundle.fixture(), consolidator=consolidator, profile="lean",
        clock=Clock(), sleep=lambda _s: None), clock=Clock())
    cid = make_claim(database, "unphotographed_part", cost_tables)
    pipeline.drain()
    with database.session() as db:
        job = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.task == "consolidate")).mappings().one()
        assert db.execute(select(func.count()).select_from(assessments)).scalar() == 0
    assert (job["state"], job["reason_code"]) == ("dead_lettered", "cost_table_unavailable")

    monkeypatch.setenv("CMEV_COST_TABLE_PATH", str(empty))
    app = create_app("sqlite:///" + str(tmp_path / "noseed.db"), tmp_path / "evidence")
    with TestClient(app) as client:
        ready = client.get("/api/v1/readyz")
        assert ready.status_code == 503 and ready.json()["failing"] == ["cost_table"]
        client.post("/api/v1/auth/login", json={"email": "surveyor@claim-cmev.demo", "password": "Demo2026!"})
        assert client.get("/api/v1/claims").json()["items"] == []


def test_schema_constraints_hold(database):
    assert check_head(database.engine) == "0001_runtime_schema"
    assert database.initialize() == "0001_runtime_schema"  # re-running the upgrade is a no-op
    row = dict(job_key="01K6F1XTVRE00000000000C003:1:intake:all:00000000", claim_id="01K6F1XTVRE00000000000C003",
               input_revision=1, task="intake", target="all", version_signature="00000000", stage="intake",
               state="pending", attempt_count=0, attempt_epoch=0, versions={"code": "0.2.0"},
               created_at=Clock()(), updated_at=Clock()())
    with database.session.begin() as db:
        db.execute(insert(jobs).values(**row))
    with pytest.raises(IntegrityError), database.session.begin() as db:
        db.execute(insert(jobs).values(**{**row, "job_key": row["job_key"] + "x"}))  # same identity, other key
    with pytest.raises(IntegrityError), database.session.begin() as db:
        db.execute(insert(jobs).values(**{**row, "state": "finished"}))
    with database.session.begin() as db:
        db.execute(insert(assessments).values(
            claim_id=row["claim_id"], assessment_revision=1, input_revision=1, state="ready", superseded=False,
            trigger="branches_complete", job_key=row["job_key"], cost_table_version="t", rules_config_version="r",
            body={}, inputs={}, reuse_lineage=[], created_at=Clock()()))
    for statement in (update(assessments).values(state="incomplete"), assessments.delete()):
        with pytest.raises(Exception, match="insert-only"), database.session.begin() as db:
            db.execute(statement)
    with database.session() as db:
        assert db.execute(text("SELECT COUNT(*) FROM assessments")).scalar() == 1
        assert get(db, "claim:none") is None
