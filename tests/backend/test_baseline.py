"""API/worker integration with isolated SQLite and local evidence; no ML/Kafka claims."""
from copy import deepcopy
from io import BytesIO
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select
from claim_cmev.api.main import create_app
from claim_cmev.runtime import Record, Outbox, get, put
from claim_cmev.worker import local_tick, process_event, record_failure


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CMEV_STORAGE_BACKEND", "local")
    monkeypatch.setenv("CMEV_FIXTURE_MODE", "true")
    monkeypatch.setenv("CMEV_DEMO_PASSWORD", "Demo2026!")
    app = create_app("sqlite:///" + str(tmp_path / "test.db"), tmp_path / "evidence")
    with TestClient(app) as client:
        yield client, app.state.database


def login(client):
    result = client.post("/api/v1/auth/login", json={"email": "surveyor@claim-cmev.demo", "password": "Demo2026!"})
    assert result.status_code == 200
    return result


def post(client, path, body, key=None):
    return client.post("/api/v1" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


def sample(client, reference="CLM-24019"):
    return next(c for c in client.get("/api/v1/claims").json()["items"] if c["reference"] == reference)


def png():
    data = BytesIO()
    Image.new("RGB", (640, 640), "white").save(data, format="PNG")
    return data.getvalue()


def upload(client, cid, role="photograph", raw=None, media="image/png", name="sample.png"):
    return client.post(f"/api/v1/claims/{cid}/files", data={"role": role}, files={"files": (name, raw if raw is not None else png(), media)}, headers={"Idempotency-Key": str(uuid4())})


def test_auth_csrf_session_and_public_docs(setup):
    client, db = setup
    assert client.get("/api/v1/claims").status_code == 401
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/api/v1/docs").status_code == 200
    assert client.get("/api/v1/readyz").json()["status"] == "ready"
    assert client.post("/api/v1/auth/login", json={"email": "bad", "password": "bad"}).status_code == 401
    response = login(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.post("/api/v1/auth/logout", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_create_idempotency_and_claim_ownership(setup):
    client, database = setup
    login(client)
    body = {"reference": "NEW-1", "vehicle_make": "Toyota", "vehicle_model": "Yaris", "vehicle_year": 2020, "vehicle_class": "hatchback"}
    first = post(client, "/claims", body, "create-1")
    assert first.status_code == 201
    assert post(client, "/claims", body, "create-1").json() == first.json()
    assert post(client, "/claims", {**body, "reference": "NEW-2"}, "create-1").status_code == 409
    assert post(client, "/claims", body).status_code == 409
    assert post(client, "/claims", {**body, "reference": " "}).status_code == 422
    assert client.post("/api/v1/claims", json=body).status_code == 400
    cid = first.json()["claim_id"]
    with database.session.begin() as db:
        row = db.get(Record, "claim:" + cid)
        row.data = {**row.data, "owner_id": "another-user"}
    assert client.get("/api/v1/claims/" + cid).status_code == 404
    assert not any(c["claim_id"] == cid for c in client.get("/api/v1/claims").json()["items"])


def test_upload_decode_dedup_cross_claim_and_outbox(setup):
    client, database = setup
    login(client)
    cid = sample(client, "CLM-24021")["claim_id"]
    assert upload(client, cid, raw=b"not a png").status_code == 415
    assert upload(client, cid, raw=b"\x89PNG\r\n\x1a\ncorrupt").status_code == 422
    assert upload(client, cid, media="image/jpeg").status_code == 415
    assert client.get(f"/api/v1/claims/{cid}/files").json() == {"files": []}
    result = upload(client, cid)
    assert result.status_code == 201
    fid = result.json()["files"][0]["file_id"]
    assert upload(client, cid).json()["files"][0]["file_id"] == fid
    assert client.get(f"/api/v1/claims/{cid}/evidence/{fid}").content == png()
    other = sample(client)["claim_id"]
    assert client.get(f"/api/v1/claims/{other}/evidence/{fid}").status_code == 403
    assert post(client, f"/claims/{other}/input-revisions", {"file_ids": [fid]}).status_code == 409
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid, fid]}).status_code == 422
    result = post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid]}, "input-1")
    assert result.status_code == 202
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid]}, "input-1").json() == result.json()
    assert client.get(f"/api/v1/claims/{cid}/processing").json()["dispatch_pending"]
    local_tick(database)
    claim = client.get(f"/api/v1/claims/{cid}").json()
    assert claim["status"] == "in_review"
    assessment = client.get(f"/api/v1/claims/{cid}/assessments/{claim['assessment_revision']}").json()
    assert assessment["line_items"] == []
    assert assessment["provenance"]["source_kind"] == "fixture"
    assert assessment["damage_summary"][0]["coverage"] == "unresolved"


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "0.001", "9999999999", "oops"])
def test_invalid_decimal_never_revises_input(setup, amount):
    client, database = setup
    login(client)
    cid = sample(client)["claim_id"]
    result = post(client, f"/claims/{cid}/assessments/1/marks/mark-1/decision", {"expected_input_revision": 1, "expected_review_revision": 0, "decision": "confirm", "amount": amount})
    assert result.status_code == 422
    assert client.get(f"/api/v1/claims/{cid}").json()["input_revision"] == 1


@pytest.mark.parametrize("decision,amount,expected", [("confirm", "1000.10", "1000.10"), ("reject", None, "1250.00")])
def test_reassessment_stale_reviews_frozen_print_and_lineage(setup, decision, amount, expected):
    client, database = setup
    login(client)
    cid = sample(client)["claim_id"]
    path = f"/claims/{cid}/assessments/1"
    assert post(client, path + "/finalize", {"expected_review_revision": 0}).status_code == 412
    assert post(client, path + "/notes", {"expected_review_revision": 0, "text": "Please retain my note"}).status_code == 200
    assert post(client, path + "/notes", {"expected_review_revision": 0, "text": "Stale"}).status_code == 409
    with database.session() as db:
        original = deepcopy(get(db, f"assessment:{cid}:1"))
    result = post(client, path + "/marks/mark-1/decision", {"expected_input_revision": 1, "expected_review_revision": 1, "decision": decision, "amount": amount}, "mark-1")
    assert result.status_code == 202
    assert post(client, path + "/finalize", {"expected_review_revision": 2}).status_code == 409
    local_tick(database)
    with database.session() as db:
        assert get(db, f"assessment:{cid}:1") == original
    claim = client.get(f"/api/v1/claims/{cid}").json()
    rev = claim["assessment_revision"]
    new_path = f"/claims/{cid}/assessments/{rev}"
    a = client.get("/api/v1" + new_path).json()
    assert a["line_items"][0]["printed_amount"] == "1250.00"
    assert a["line_items"][0]["effective_amount"] == expected
    assert a["line_items"][0]["overall_result"] == "insufficient_evidence"
    review = client.get("/api/v1" + new_path + "/review").json()
    assert review["actions"][0]["text"] == "Please retain my note"
    result = post(client, new_path + "/finalize", {"expected_review_revision": 2}, "finalize-1")
    assert result.status_code == 200
    assert post(client, new_path + "/finalize", {"expected_review_revision": 2}, "finalize-1").json() == result.json()
    frozen = client.get(result.json()["print_view_url"]).json()
    assert frozen["review"]["finalized"]
    assert frozen["review"]["review_revision"] == 3
    assert post(client, new_path + "/notes", {"expected_review_revision": 3, "text": "Cannot change"}).status_code == 409
    assert client.get("/api/v1" + new_path + "/print-view?review_revision=2").status_code == 409


def test_worker_replay_and_permanent_failure(setup):
    client, database = setup
    with database.session() as db:
        event = deepcopy(db.scalar(select(Outbox)).payload)
    assert process_event(database, event)
    assert not process_event(database, event)
    assert not process_event(database, {**event, "dedup_key": "redelivered-different-envelope"})
    with database.session() as db:
        assert len(list(db.scalars(select(Record).where(Record.kind == "assessment", Record.claim_id == event["claim_id"])))) == 1
    malformed = {**event, "schema_version": "unsupported"}
    with pytest.raises(ValueError):
        process_event(database, malformed)
    record_failure(database, malformed, "unsupported_schema")
    record_failure(database, malformed, "unsupported_schema")
    record_failure(database, [], "invalid_envelope")
    with database.session() as db:
        assert get(db, "claim:" + event["claim_id"])["status"] == "in_review"
        assert len(list(db.scalars(select(Record).where(Record.kind == "dead_letter")))) == 2


def test_bad_pending_job_is_failed_not_stuck(setup):
    client, database = setup
    with database.session.begin() as db:
        row = db.scalar(select(Outbox))
        event = {**row.payload, "schema_version": "invalid"}
        row.payload = event
    local_tick(database)
    with database.session() as db:
        assert get(db, "claim:" + event["claim_id"])["status"] == "failed"
        assert get(db, "job:" + event["job_key"])["state"] == "dead_lettered"
        assert len(list(db.scalars(select(Record).where(Record.kind == "dead_letter")))) == 1


def test_kafka_poll_commits_only_after_all_partitions(monkeypatch):
    from types import SimpleNamespace
    import claim_cmev.worker as worker
    calls = []
    consumer = SimpleNamespace(
        poll=lambda **kwargs: {0: [SimpleNamespace(value="first")], 1: [SimpleNamespace(value="second")]},
        commit=lambda: calls.append("commit"))
    def fail_later(database, event):
        calls.append(event)
        if event == "second":
            raise RuntimeError("database unavailable")
    monkeypatch.setattr(worker, "process_event", fail_later)
    with pytest.raises(RuntimeError):
        worker.consume_poll(None, None, consumer)
    assert calls == ["first", "second"]
    calls.clear()
    monkeypatch.setattr(worker, "process_event", lambda database, event: calls.append(event))
    worker.consume_poll(None, None, consumer)
    assert calls == ["first", "second", "commit"]


def test_duplicate_files_in_single_upload_create_one_record(setup):
    client, database = setup
    login(client)
    cid = sample(client, "CLM-24021")["claim_id"]
    response = client.post(f"/api/v1/claims/{cid}/files", data={"role": "photograph"},
        files=[("files", ("one.png", png(), "image/png")), ("files", ("two.png", png(), "image/png"))],
        headers={"Idempotency-Key": "batch-upload"})
    assert response.status_code == 201
    files = response.json()["files"]
    assert files[0]["file_id"] == files[1]["file_id"]
    assert len(client.get(f"/api/v1/claims/{cid}/files").json()["files"]) == 1


def test_superseded_jobs_cannot_replace_current_assessment(setup):
    client, database = setup
    login(client)
    cid = sample(client, "CLM-24021")["claim_id"]
    fid = upload(client, cid, role="estimate_page").json()["files"][0]["file_id"]
    for _ in range(2):
        assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid]}).status_code == 202
    with database.session() as db:
        events = [deepcopy(row.payload) for row in db.scalars(select(Outbox).order_by(Outbox.id)) if row.payload["claim_id"] == cid]
    assert process_event(database, events[1])
    current = client.get(f"/api/v1/claims/{cid}").json()["assessment_revision"]
    assert process_event(database, events[0])
    claim = client.get(f"/api/v1/claims/{cid}").json()
    assert claim["assessment_revision"] == current
    assert claim["input_revision"] == 2
    assert claim["estimate_row_count"] == 3
    assert claim["declared_total"] == "2590.00"
    assert len(client.get(f"/api/v1/claims/{cid}/assessments").json()["items"]) == 2
    late = next(a for a in client.get(f"/api/v1/claims/{cid}/assessments").json()["items"] if a["input_revision"] == 1)
    assert not client.get(f"/api/v1/claims/{cid}/assessments/{late['assessment_revision']}/finalize-preconditions").json()["can_finalize"]


def test_old_frozen_print_is_unchanged_after_new_input(setup):
    client, database = setup
    login(client)
    cid = sample(client, "CLM-24018")["claim_id"]
    url = f"/api/v1/claims/{cid}/assessments/1/print-view?review_revision=0"
    before = client.get(url).json()
    fid = upload(client, cid).json()["files"][0]["file_id"]
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid]}).status_code == 202
    local_tick(database)
    assert client.get(url).json() == before
