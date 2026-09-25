"""Reported review failures, exercised through HTTP and fixture reassessment."""
from test_baseline import setup, login, sample, post, upload, png
from test_review_integration import current, act
from claim_cmev.worker import local_tick
from PIL import Image
from io import BytesIO

def image_bytes(color):
    data = BytesIO()
    Image.new("RGB", (640, 640), color).save(data, "PNG")
    return data.getvalue()



def test_unknown_operation_reassesses_and_explicit_empty_rejects_rows(setup):
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    _, _, a = current(client, cid)
    response = act(client, cid, action_type="correct_line_item", entry_id=a["line_items"][0]["entry_id"],
                   corrections={"operation": "unknown"}, reason_code="ocr_error")
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert c["assessment_revision"] == 2, client.get(f"/api/v1/claims/{cid}/processing").text
    assert a["line_items"][0]["overall_result"] == "insufficient_evidence"
    response = act(client, cid, action_type="confirm_declaration_completeness",
                   completeness_state="explicitly_empty", reason_code="no_declared_repairs")
    assert response.status_code == 422, response.text
    assert current(client, cid)[0]["input_revision"] == 2


def test_identity_rerun_keeps_existing_view_signals_and_adequate_coverage(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    before = {(c["part_code"], c["side"]) for c in a["coverage"] if c["state"] == "adequate"}
    slot = next(c for c in a["coverage"] if c["state"] == "adequate")
    response = act(client, cid, action_type="confirm_identity", part_code=slot["part_code"],
                   side=slot["side"], photo_ids=slot["covering_photo_ids"])
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert c["assessment_revision"] == 2
    after = {(c["part_code"], c["side"]) for c in a["coverage"] if c["state"] == "adequate"}
    assert before <= after


def test_extra_unreadable_page_withholds_additions(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    files = []
    for role, name in (("photograph", "p.png"), ("estimate_page", "one.png"), ("estimate_page", "two.png")):
        files.append(upload(client, cid, role=role, name=name, raw=image_bytes("green" if name == "two.png" else "blue")).json()["files"][0]["file_id"])
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": files}).status_code == 202
    local_tick(db)
    _, _, a = current(client, cid)
    assert any(p["quality"]["state"] == "unreadable" for p in a["pages"])
    assert a["declaration"]["state"] == "partial"
    assert a["declaration"]["unparsed_region_count"] > 0
    assert not any(c["status"] == "proposed" for c in a["possible_additions"])
    assert not a["finalize_preconditions"]["can_finalize"]
    assert any(not check["passed"] and "completeness" in check["code"]
               for check in a["finalize_preconditions"]["checks"])


def test_adding_photo_preserves_document_decision_and_note(setup):
    client, db = setup
    login(client)
    cid = sample(client, "CLM-24021")["claim_id"]
    files = [upload(client, cid, role=role, name=name, raw=image_bytes("green" if name == "two.png" else "blue")).json()["files"][0]["file_id"]
             for role, name in (("photograph", "p.png"), ("estimate_page", "page.png"))]
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": files}).status_code == 202
    local_tick(db)
    _, _, a = current(client, cid)
    mark = a["marks"][0]
    assert act(client, cid, action_type="confirm_mark", mark_id=mark["mark_id"],
               target_entry_id=mark["candidate_entry_ids"][0], amount="400.00").status_code == 202
    local_tick(db)
    assert act(client, cid, action_type="add_note", note="Retain across photo upload").status_code == 200
    files.append(upload(client, cid, name="another.png", raw=image_bytes("red")).json()["files"][0]["file_id"])
    assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": files}).status_code == 202
    local_tick(db)
    c, path, a = current(client, cid)
    assert c["assessment_revision"] == 3, client.get(f"/api/v1/claims/{cid}/processing").text
    mark = next(m for m in a["marks"] if m["mark_id"] == mark["mark_id"])
    assert mark["state"] == "confirmed" and mark["confirmed_amount"] == "400.00"
    actions = client.get("/api/v1" + path + "/review").json()["actions"]
    assert any(a.get("text") == "Retain across photo upload" for a in actions)


def test_photos_only_and_pages_only_can_freeze_with_missing_evidence_reasons(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    for role in ("photograph", "estimate_page"):
        fid = upload(client, cid, role=role, raw=image_bytes("red"), name=role+".png").json()["files"][0]["file_id"]
        assert post(client, f"/claims/{cid}/input-revisions", {"file_ids": [fid]}).status_code == 202
        local_tick(db)
        c, path, a = current(client, cid)
        assert a["state"] == "incomplete"
        assert a["finalize_preconditions"]["can_finalize"], a["finalize_preconditions"]
        response = post(client, path+"/finalize", {"expected_review_revision": c["review_revision"]})
        assert response.status_code == 200, response.text
        printed = client.get(response.json()["print_view_url"])
        assert printed.status_code == 200
        assert printed.json()["assessment"]["incomplete_reasons"]


def test_login_failures_for_another_account_do_not_lock_out_proxy_peers(setup):
    client, _ = setup
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "wrong"}).status_code == 429
    login(client)
    assert client.post("/api/v1/auth/login", json={"email": "surveyor@claim-cmev.demo", "password": "\u00e9"}).status_code == 401


def test_corrected_printed_amount_preserves_original_through_repeated_edits_and_freeze(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    entry = next(r for r in a["line_items"] if r["row_state"] != "excluded")
    original = entry["printed_amount"]
    for value in ("450.00", "460.00"):
        response = act(client, cid, action_type="correct_line_item", entry_id=entry["entry_id"],
                       corrections={"printed_line_amount": value}, reason_code="ocr_error")
        assert response.status_code == 202, response.text
        local_tick(db)
        c, path, a = current(client, cid)
        row = next(r for r in a["line_items"] if r["entry_id"] == entry["entry_id"])
        assert row["printed_amount"] == value
        assert row["original_printed_amount"] == original and row["printed_amount_corrected"]
    response = post(client, path+"/finalize", {"expected_review_revision": c["review_revision"]})
    assert response.status_code == 200, response.text
    report = client.get(response.json()["print_view_url"]).json()["report"]
    row = next(r for r in report["line_items"] if r["entry_id"] == entry["entry_id"])
    assert row["original_printed_line_amount"] == original
    assert row["printed_line_amount"] == "460.00"


def test_accept_addition_updates_missing_scope_without_inventing_document_rows(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, before = current(client, cid)
    candidate = next(c for c in before["possible_additions"] if c["status"] == "proposed")
    response = act(client, cid, action_type="accept_addition", candidate_id=candidate["candidate_id"],
                   operation="repair", quantity="1", reason_code="workshop_to_quote")
    assert response.status_code == 202, response.text
    local_tick(db)
    _, _, after = current(client, cid)
    assert not any((c["part_code"], c["side"]) == (candidate["part_code"], candidate["side"])
                   for c in after["possible_additions"])
    assert len(after["line_items"]) == len(before["line_items"])
    assert after["review_overlay"]["accepted_scope"][0]["new_values"]["amount"] is None


def test_identity_audit_before_value_uses_previous_human_confirmation(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    slot = next(c for c in a["coverage"] if c["state"] == "adequate")
    values = dict(action_type="confirm_identity", part_code=slot["part_code"], side=slot["side"],
                  photo_ids=slot["covering_photo_ids"])
    assert act(client, cid, **values).status_code == 202
    local_tick(db)
    response = act(client, cid, **values)
    assert response.status_code == 202, response.text
    before = response.json()["event"]["original_values"]
    assert before["side"] == slot["side"] and before["source"] == "human"
    assert before["identities"]


def test_claim_list_batch_reads_match_individual_views_without_query_growth(setup):
    from sqlalchemy import event, select
    from claim_cmev.api.views import claim_views, claim_view
    from claim_cmev.runtime import Record
    _, db = setup
    local_tick(db)
    with db.session() as session:
        claims = [r.data for r in session.scalars(select(Record).where(Record.kind == "claim"))]
        expected = [claim_view(session, c) for c in claims]
        calls = []
        def count(_connection, _cursor, sql, *_args):
            if sql.lstrip().upper().startswith("SELECT"):
                calls.append(sql)
        event.listen(db.engine, "before_cursor_execute", count)
        try:
            assert claim_views(session, claims * 5) == expected * 5
            assert len(calls) <= 5
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
