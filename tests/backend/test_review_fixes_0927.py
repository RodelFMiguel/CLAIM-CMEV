"""Regressions for the 2026-09-27 verification of 6e59bc2 (backend review, orchestration and API lane).

Each test fails on the 6e59bc2 behaviour and passes after the fix it names.
"""
import re

from sqlalchemy import event, select
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from test_baseline import setup, login, sample, post, upload  # noqa: F401 - pytest fixture
from test_review_integration import current, act
from test_review_defects import image_bytes
from claim_cmev.api.main import create_app
from claim_cmev.persistence.tables import assessments, dead_letters
from claim_cmev.worker import local_tick

USER = "surveyor@claim-cmev.demo"


def stored(db, cid, rev):
    with db.session() as session:
        return session.execute(select(assessments.c.inputs, assessments.c.body).where(
            assessments.c.claim_id == cid, assessments.c.assessment_revision == rev)).one()


def uploaded_claim(client, db, reference="CLM-24020", photo="red", page="blue"):
    cid = sample(client, reference)["claim_id"]
    ph = upload(client, cid, raw=image_bytes(photo), name=f"{photo}.png").json()["files"][0]["file_id"]
    pg = upload(client, cid, role="estimate_page", raw=image_bytes(page), name=f"{page}.png").json()["files"][0]["file_id"]
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [ph, pg]}).status_code == 202
    local_tick(db)
    return cid, ph, pg


# ---------------------------------------------------------------- N1: row corrections never dead-letter
def test_part_only_correction_to_a_sided_part_leaves_side_unknown_and_reassesses(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    row = next(r for r in a["line_items"] if r["side"] == "not_applicable" and r["row_state"] != "excluded")
    response = act(client, cid, action_type="correct_line_item", entry_id=row["entry_id"],
                   corrections={"part_code": "fender"}, reason_code="ocr_error")
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert (c["status"], c["assessment_revision"]) == ("in_review", 2), client.get(
        f"/api/v1/claims/{cid}/processing").text
    corrected = next(r for r in a["line_items"] if r["entry_id"] == row["entry_id"])
    assert (corrected["part_code"], corrected["side"]) == ("fender", "unknown")  # never inferred
    assert corrected["overall_result"] == "insufficient_evidence"
    with db.session() as session:
        assert not session.execute(select(dead_letters)).first()


def test_a_correction_that_forms_no_valid_row_is_refused_at_the_action_boundary(setup, monkeypatch):
    from claim_cmev.contracts.common import ContractError
    from claim_cmev.orchestration import corrections

    def invalid(*_args, **_kwargs):
        raise ContractError("line_item_invalid", "a physical side requires a source")

    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    c, _, a = current(client, cid)
    monkeypatch.setattr(corrections, "correct_line_item", invalid)
    response = act(client, cid, action_type="correct_line_item", entry_id=a["line_items"][0]["entry_id"],
                   corrections={"operation": "repair"}, reason_code="ocr_error")
    assert response.status_code == 422, response.text
    assert response.json()["reason_code"] == "correction_invalid"
    assert current(client, cid)[0]["input_revision"] == c["input_revision"]


# ---------------------------------------------------------------- Partial 1: unknown on an unresolved field
def test_unknown_on_an_unresolved_part_keeps_the_parser_status_and_reason(setup):
    client, db = setup
    login(client)
    cid, _, _ = uploaded_claim(client, db, "CLM-24021")
    _, _, a = current(client, cid)
    inputs, _ = stored(db, cid, a["assessment_revision"])
    row = next(i for i in inputs["line_items"] if i["part_mapping_status"] == "ambiguous")
    response = act(client, cid, action_type="correct_line_item", entry_id=row["entry_id"],
                   corrections={"part_code": "unknown", "operation": "replace"}, reason_code="mapping_error")
    assert response.status_code == 202, response.text
    assert "part_code" not in response.json()["event"]["new_values"]
    local_tick(db)
    c, _, _ = current(client, cid)
    inputs, _ = stored(db, cid, c["assessment_revision"])
    item = next(i for i in inputs["line_items"] if i["entry_id"] == row["entry_id"])
    assert (item["part_code"], item["part_mapping_status"]) == (None, "ambiguous")
    assert {"field": "part_code", "reason": "ocr_low_confidence"} in item["field_uncertainty"]
    assert (item["operation"], item["operation_mapping_status"]) == ("replace", "resolved")
    only_unknown = act(client, cid, action_type="correct_line_item", entry_id=row["entry_id"],
                       corrections={"part_code": "unknown"}, reason_code="mapping_error")
    assert only_unknown.status_code == 422 and only_unknown.json()["reason_code"] == "no_change"


# ---------------------------------------------------------------- N2 and not-fixed 1: human precedence
def test_surveyor_can_correct_their_own_identity_side(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    slot = next(c for c in a["coverage"] if c["part_code"] == "fender")
    photos = [v["photo_id"] for v in slot["views"]]
    for side in ("right", "left"):
        assert act(client, cid, action_type="confirm_identity", part_code="fender", side=side,
                   photo_ids=photos).status_code == 202
        local_tick(db)
        _, _, a = current(client, cid)
        assert {(c["part_code"], c["side"]) for c in a["coverage"] if c["part_code"] == "fender"} == {("fender", side)}
    row = next(r for r in a["line_items"] if r["description"].startswith("FENDER LH"))
    assert "side_unresolved" not in row["reason_codes"]
    review = client.get(f"/api/v1/claims/{cid}/assessments/{a['assessment_revision']}/review").json()
    assert [x["new_values"]["side"] for x in review["actions"] if x.get("action_type") == "confirm_identity"] == \
        ["right", "left"]  # both statements stay in the audit history


def test_a_surveyor_coverage_decline_outranks_the_fixture_baseline(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    slot = next(c for c in a["coverage"] if c["part_code"] == "front-bumper")
    assert slot["state"] == "adequate"
    assert act(client, cid, action_type="confirm_coverage", part_code="front-bumper", side=slot["side"],
               photo_ids=slot["covering_photo_ids"], covers_enough=False,
               reason_code="view_obstructed").status_code == 202
    local_tick(db)
    c, _, a = current(client, cid)
    after = next(x for x in a["coverage"] if x["part_code"] == "front-bumper")
    assert (after["state"], after["reasons"]) == ("inadequate", ["view_obstructed"])
    assert c["assessment_revision"] == 2 and after["coverage_confirmation_id"] != slot["coverage_confirmation_id"]


# ---------------------------------------------------------------- N3 and N4: accepted scope lineage
def test_accepted_addition_is_invalidated_when_its_photographs_change(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid, _, page = uploaded_claim(client, db)
    _, _, a = current(client, cid)
    candidate = next(x for x in a["possible_additions"] if x["status"] == "proposed")
    assert act(client, cid, action_type="accept_addition", candidate_id=candidate["candidate_id"], operation="repair",
               quantity="1", reason_code="workshop_to_quote").status_code == 202
    local_tick(db)
    photo2 = upload(client, cid, raw=image_bytes("green"), name="new.png").json()["files"][0]["file_id"]
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [photo2, page]}).status_code == 202
    local_tick(db)
    c, _, a = current(client, cid)
    invalidated = a["invalidated_corrections"]
    assert [(x["event"]["action_type"], x["invalidation_reason"]) for x in invalidated] == \
        [("accept_addition", "source_photographs_changed")]
    assert a["review_overlay"]["accepted_scope"] == []
    inputs, _ = stored(db, cid, c["assessment_revision"])
    assert inputs["accepted_scope"] == []
    assert any((x["part_code"], x["side"]) == (candidate["part_code"], candidate["side"])
               for x in a["possible_additions"])  # proposed again on the new evidence


def test_upload_after_an_unreviewed_reassessment_keeps_history_and_shows_carried_scope(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid, photo, page = uploaded_claim(client, db)
    _, _, a = current(client, cid)
    accepted_on = a["assessment_revision"]
    candidate = next(x for x in a["possible_additions"] if x["status"] == "proposed")
    assert act(client, cid, action_type="accept_addition", candidate_id=candidate["candidate_id"], operation="repair",
               quantity="1", reason_code="workshop_to_quote").status_code == 202
    local_tick(db)  # the reassessment is never reviewed: no stored review for it
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [photo, page]}).status_code == 202
    local_tick(db)
    c, path, a = current(client, cid)
    assert c["assessment_revision"] == accepted_on + 2
    actions = client.get("/api/v1" + path + "/review").json()["actions"]
    assert [(x["action_type"], x["assessment_revision"]) for x in actions] == [("accept_addition", accepted_on)]
    scope = a["review_overlay"]["accepted_scope"]
    assert [(e["new_values"]["part_code"], e["new_values"]["side"]) for e in scope] == \
        [(candidate["part_code"], candidate["side"])]
    inputs, _ = stored(db, cid, c["assessment_revision"])
    assert [e["action_id"] for e in inputs["accepted_scope"]] == [e["action_id"] for e in scope]


# ---------------------------------------------------------------- Partial 2: completeness semantics
def test_completeness_confirmation_preserves_the_unparsed_region_count(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    files = [upload(client, cid, role=role, name=name, raw=image_bytes(color)).json()["files"][0]["file_id"]
             for role, name, color in (("photograph", "p.png", "blue"), ("estimate_page", "one.png", "blue"),
                                       ("estimate_page", "two.png", "green"))]
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": files}).status_code == 202
    local_tick(db)
    _, _, a = current(client, cid)
    unparsed = a["declaration"]["unparsed_region_count"]
    assert a["declaration"]["state"] == "partial" and unparsed > 0
    assert act(client, cid, action_type="confirm_declaration_completeness",
               completeness_state="complete").status_code == 202
    local_tick(db)
    _, _, a = current(client, cid)
    assert (a["declaration"]["state"], a["declaration"]["source"]) == ("complete", "human_confirmation")
    assert a["declaration"]["unparsed_region_count"] == unparsed


def test_explicitly_empty_confirmation_uses_the_m5_helper_semantics():
    from datetime import UTC, datetime
    from claim_cmev.contracts.common import Provenance
    from claim_cmev.contracts.documents import DeclarationCompleteness
    from claim_cmev.contracts.review import ReviewEvent
    from claim_cmev.orchestration.corrections import confirm_completeness
    parser = DeclarationCompleteness(
        claim_id="01K50000000000000000000009", input_revision=1, provenance=Provenance(
            source_kind="fixture", runtime_profile="lean", producer_service="cmev-worker-line-items"),
        versions={"m5": "test"}, state="partial", reasons=["no_rows_matched"], unparsed_region_count=2,
        layout_family="family-a-ruled-grid", pages_covered=["dp-1"], source="parser")
    at = datetime(2026, 9, 27, tzinfo=UTC)
    event = ReviewEvent(event_id="rve-1", action_id="ra-1", claim_id=parser.claim_id, assessment_revision=1,
                        expected_review_revision=0, resulting_review_revision=1, actor="surveyor", recorded_at=at,
                        action_type="confirm_declaration_completeness", reason_code="no_declared_repairs",
                        original_values={}, new_values={"state": "explicitly_empty", "source": "human_confirmation",
                                                        "reasons": ["no_declared_repairs"]},
                        idempotency_key="idem-key-0001")
    human = Provenance(source_kind="real", runtime_profile="lean", producer_service="cmev-api")
    record = confirm_completeness(parser, event, items=[], revision=2, provenance=human)
    assert (record.state, record.reasons, record.unparsed_region_count) == ("explicitly_empty",
                                                                             ["no_declared_repairs"], 2)
    assert (record.source, record.confirmed_by, record.review_revision) == ("human_confirmation", "surveyor", 1)


# ---------------------------------------------------------------- Partial 6: prior assessment after a failure
def test_latest_assessment_stays_readable_and_flagged_after_a_failed_reassessment(setup, monkeypatch):
    from claim_cmev.contracts.common import ContractError
    from claim_cmev.orchestration import consolidation

    def broken(*_args, **_kwargs):
        raise ContractError("simulated_failure", "consolidation failed")

    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    assert act(client, cid, action_type="correct_line_item", entry_id=current(client, cid)[2]["line_items"][0]["entry_id"],
               corrections={"operation": "repair"}, reason_code="ocr_error").status_code == 202
    monkeypatch.setattr(consolidation, "consolidate", broken)
    local_tick(db)
    claim = client.get(f"/api/v1/claims/{cid}").json()
    assert (claim["status"], claim["assessment_revision"], claim["latest_assessment_revision"]) == ("incomplete", None, 1)
    assert claim["discrepancy_count"] is None and claim["information_needed_count"] is None
    view = client.get(f"/api/v1/claims/{cid}/assessments/1").json()
    assert (view["is_current"], view["is_latest"], view["read_only"], view["not_current_reason"]) == \
        (False, True, True, "reassessment_failed")
    assert view["line_items"] and not view["finalize_preconditions"]["can_finalize"]


# ---------------------------------------------------------------- not-fixed 2: status-aware queue counts
def test_claim_list_reports_discrepancy_and_information_counts_only_for_a_current_result(setup):
    client, db = setup
    login(client)
    items = {c["reference"]: c for c in client.get("/api/v1/claims").json()["items"]}
    assert items["CLM-24020"]["status"] == "processing"
    assert items["CLM-24020"]["discrepancy_count"] is None and items["CLM-24020"]["information_needed_count"] is None
    assert items["CLM-24021"]["discrepancy_count"] is None  # awaiting upload
    local_tick(db)
    for claim in client.get("/api/v1/claims").json()["items"]:
        if claim["assessment_revision"] is None:
            continue
        _, body = stored(db, claim["claim_id"], claim["assessment_revision"])
        results = [f["overall_result"] for f in body["findings"] if f["row_state"] != "excluded"]
        assert claim["discrepancy_count"] == sum(r in ("unsupported", "cost_outlier") for r in results)
        assert claim["information_needed_count"] == results.count("insufficient_evidence")
        assert claim["finding_count"] == claim["discrepancy_count"] + claim["information_needed_count"]
        detail = client.get(f"/api/v1/claims/{claim['claim_id']}").json()
        assert (detail["discrepancy_count"], detail["information_needed_count"]) == \
            (claim["discrepancy_count"], claim["information_needed_count"])


# ---------------------------------------------------------------- Partial 9: one load per request
def test_assessment_detail_loads_the_assessment_and_claim_view_once(setup, monkeypatch):
    from claim_cmev.api import views
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    c = client.get(f"/api/v1/claims/{cid}").json()
    calls, statements = [], []
    real = views.claim_view
    monkeypatch.setattr(views, "claim_view", lambda *a, **k: calls.append(1) or real(*a, **k))

    def count(_conn, _cursor, sql, *_args):
        if sql.lstrip().upper().startswith("SELECT") and re.search(r"FROM\s+(\w+\.)?assessments\b", sql):
            statements.append(sql)
    event.listen(db.engine, "before_cursor_execute", count)
    try:
        assert client.get(f"/api/v1/claims/{cid}/assessments/{c['assessment_revision']}").status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert len(calls) == 1
    assert len(statements) == 1, statements


# ---------------------------------------------------------------- Partial 8 and N7: login limiter
def test_an_attacker_elsewhere_cannot_lock_the_surveyor_out(tmp_path):
    app = create_app("sqlite:///" + str(tmp_path / "login.db"), tmp_path / "evidence")
    # uvicorn's proxy-header support as configured in infra/Dockerfile.api; nginx sets the header.
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="testclient")
    with TestClient(proxied) as client:
        def attempt(ip, password, email=USER):
            return client.post("/api/v1/auth/login", json={"email": email, "password": password},
                               headers={"X-Forwarded-For": ip}).status_code
        codes = [attempt("203.0.113.9", f"guess{i}", USER.upper() if i % 2 else USER) for i in range(12)]
        assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]  # brute force stays limited per account
        assert attempt("203.0.113.9", "Demo2026!") == 429  # even the right password, from that address
        assert attempt("198.51.100.7", "Demo2026!") == 200  # the surveyor, from their own address
        assert attempt("198.51.100.7", "wrong") == 401


def test_login_failure_store_is_bounded_and_expires():
    from claim_cmev.api.ratelimit import LoginFailures
    now = [0.0]
    store = LoginFailures(limit=3, window=60, max_keys=100, clock=lambda: now[0])
    for i in range(500):
        store.record_failure(f"10.0.{i // 250}.{i % 250}", f"{i}-" + "x" * 240)
    assert len(store) == 100
    for _ in range(3):
        store.record_failure("192.0.2.1", USER)
    assert store.blocked("192.0.2.1", USER.upper()) and not store.blocked("192.0.2.2", USER)
    now[0] = 61.0
    assert not store.blocked("192.0.2.1", USER) and len(store) == 0


def test_deployment_forwards_the_real_client_address_to_the_login_limiter():
    """Behind nginx every browser shared nginx's address; the limiter then locked the account."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "infra"
    dockerfile, nginx = (root / "Dockerfile.api").read_text(), (root / "nginx.conf").read_text()
    assert "--forwarded-allow-ips" in dockerfile and "--proxy-headers" in dockerfile
    assert re.search(r"FORWARDED_ALLOW_IPS=\S*172\.16\.0\.0/12", dockerfile)
    # Overwritten, never appended: a client-supplied X-Forwarded-For cannot pick the bucket.
    assert re.search(r"proxy_set_header X-Forwarded-For \$remote_addr;", nginx)
    assert "$proxy_add_x_forwarded_for" not in nginx
