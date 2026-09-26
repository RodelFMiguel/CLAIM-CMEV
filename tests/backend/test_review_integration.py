"""Review HTTP -> immutable input -> real rules over fixtures -> frozen report."""
from copy import deepcopy
from sqlalchemy import select
from test_baseline import setup, login, sample, post
from claim_cmev.worker import local_tick
from claim_cmev.persistence.tables import assessments, jobs
from claim_cmev.runtime import get, put


def current(client, cid):
    c = client.get(f"/api/v1/claims/{cid}").json()
    path = f"/claims/{cid}/assessments/{c['assessment_revision']}"
    return c, path, client.get("/api/v1" + path).json()


def act(client, cid, **values):
    c, path, _ = current(client, cid)
    return post(client, path + "/review-actions", dict(
        expected_review_revision=c["review_revision"], expected_input_revision=c["input_revision"], **values))


def test_row_correction_completeness_note_and_print(setup):
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    _, _, a = current(client, cid)
    original = deepcopy(a)
    entry = a["line_items"][0]["entry_id"]
    assert act(client, cid, action_type="add_note", note="Keep this across reassessments").status_code == 200
    response = act(client, cid, action_type="correct_line_item", entry_id=entry,
                   corrections={"operation": "repair", "side": "not_applicable", "printed_line_amount": "1050.00"},
                   reason_code="ocr_error")
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert c["input_revision"] == 2
    assert a["line_items"][0]["printed_amount"] == "1050.00"
    assert a["line_items"][0]["effective_amount"] is None  # pending repricing still withholds
    assert act(client, cid, action_type="reject_mark", mark_id=a["marks"][0]["mark_id"]).status_code == 202
    local_tick(db)
    assert act(client, cid, action_type="confirm_declaration_completeness",
               completeness_state="complete").status_code == 202
    local_tick(db)
    c, path, a = current(client, cid)
    assert a["declaration"]["source"] == "human_confirmation"
    assert a["line_items"][0]["effective_amount"] == "1050.00"
    result = post(client, path + "/finalize", {"expected_review_revision": c["review_revision"]})
    assert result.status_code == 200, result.text
    frozen = client.get(result.json()["print_view_url"]).json()
    assert frozen["report"]["notes"][0]["note"] == "Keep this across reassessments"
    assert frozen["report"]["header"]["review_revision"] == c["review_revision"]
    with db.session() as session:
        old = session.execute(select(assessments.c.body).where(
            assessments.c.claim_id == cid, assessments.c.assessment_revision == 1)).scalar_one()
    assert old["findings"] == original["findings"]


def test_identity_and_coverage_rerun_summary_only_with_original_observations(setup):
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    _, _, a = current(client, cid)
    summary = next(s for s in a["part_summaries"] if s["part_code"])
    photo = summary["supporting_photo_ids"][0]
    obs = {o["observation_id"] for o in a["observations"]}
    response = act(client, cid, action_type="confirm_identity", part_code=summary["part_code"],
                   side="not_applicable", photo_ids=[photo])
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert c["assessment_revision"] == 2, client.get(f"/api/v1/claims/{cid}/processing").text
    assert {o["observation_id"] for o in a["observations"]} == obs
    assert any(s["identity_status"] == "resolved" for s in a["part_summaries"])
    response = act(client, cid, action_type="confirm_coverage", part_code=summary["part_code"],
                   side="not_applicable", photo_ids=[photo], covers_enough=False, reason_code="cropped")
    assert response.status_code == 202, response.text
    local_tick(db)
    c, _, a = current(client, cid)
    assert c["assessment_revision"] == 3
    slot = next(x for x in a["coverage"] if x["part_code"] == summary["part_code"] and x["side"] == "not_applicable")
    assert slot["state"] != "adequate"
    with db.session() as session:
        stages = session.execute(select(jobs.c.stage).where(jobs.c.claim_id == cid, jobs.c.input_revision == 3)).scalars().all()
    assert set(stages) == {"intake", "summary", "consolidate"}


def test_accept_addition_keeps_human_operation_and_absent_amount(setup):
    client, db = setup
    login(client)
    local_tick(db)
    cid = sample(client, "CLM-24020")["claim_id"]
    _, _, a = current(client, cid)
    candidate = next(x for x in a["possible_additions"] if x["status"] == "proposed")
    response = act(client, cid, action_type="accept_addition", candidate_id=candidate["candidate_id"],
                   operation="repair", quantity="1", reason_code="workshop_to_quote")
    assert response.status_code == 202, response.text
    local_tick(db)
    c, path, a = current(client, cid)
    assert c["assessment_revision"] == 2
    result = post(client, path + "/finalize", {"expected_review_revision": c["review_revision"]})
    assert result.status_code == 200, result.text
    report = client.get(result.json()["print_view_url"]).json()["report"]
    accepted = report["accepted_additions"][0]
    assert accepted["operation"] == "repair" and accepted["amount"] is None
    assert accepted["amount_absent_reason"] == "workshop_to_quote"
    assert not any(x["part_code"] == accepted["part_code"] and x["side"] == accepted["side"] for x in report["open_additions"])


def test_added_mark_amount_edit_and_dismissal_overlay(setup):
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    _, _, a = current(client, cid)
    entry = a["line_items"][0]
    assert act(client, cid, action_type="reject_mark", mark_id=a["marks"][0]["mark_id"]).status_code == 202
    local_tick(db)
    response = act(client, cid, action_type="add_mark", entry_id=entry["entry_id"], page_id=entry["page_id"],
                   box_norm=[.75, .2, .9, .25], mark_type="price_change", amount="900.00")
    assert response.status_code == 202, response.text
    local_tick(db)
    _, _, a = current(client, cid)
    assert a["line_items"][0]["effective_amount"] == "900.00"
    mark = next(m for m in a["marks"] if m["origin"] == "human_added")
    assert act(client, cid, action_type="enter_amount", mark_id=mark["mark_id"],
               amount="850.00").status_code == 202
    local_tick(db)
    c, _, a = current(client, cid)
    assert a["line_items"][0]["effective_amount"] == "850.00"
    finding = a["findings"][0]
    response = act(client, cid, action_type="dismiss_finding", finding_id=finding["finding_id"],
                   reason_code="inadequate_photograph")
    assert response.status_code == 200, response.text
    c2, _, a2 = current(client, cid)
    assert c2["input_revision"] == c["input_revision"]
    assert a2["findings"] == a["findings"]
    assert finding["finding_id"] in a2["review_overlay"]["dismissals"]
    # Completeness confirmation does not change this finding's checks or evidence.
    assert act(client, cid, action_type="confirm_declaration_completeness", completeness_state="complete").status_code == 202
    local_tick(db)
    c3, _, a3 = current(client, cid)
    new_id = a3["findings"][0]["finding_id"]
    assert a3["review_overlay"]["dismissals"][new_id]["carried_from"] == finding["finding_id"]


def test_stale_generic_request_retains_values_and_replay_is_exact(setup):
    client, db = setup
    login(client)
    cid = sample(client)["claim_id"]
    c, path, a = current(client, cid)
    payload = {"action_type": "add_note", "note": "Original request", "expected_review_revision": 0}
    result = post(client, path + "/review-actions", payload, "retry-test")
    replay = post(client, path + "/review-actions", payload, "retry-test")
    assert result.status_code == replay.status_code == 200
    assert result.json() == replay.json()
    conflict = post(client, path + "/review-actions", {**payload, "note": "Local draft"}, "new-request")
    assert conflict.status_code == 409
    assert "Local draft" in conflict.text
    review = client.get("/api/v1" + path + "/review").json()
    assert len(review["actions"]) == 1
