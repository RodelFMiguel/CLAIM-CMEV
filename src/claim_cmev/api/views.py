"""Read models for the API: claim status, processing state and the assessment view.

Claim status is derived, never stored by the workers: the consolidator moves
``assessment.current_pointer`` and the orchestrator keeps ``ops.branch_state``; the
API combines them with its own claim and review records. The assessment view returns the
immutable contract ``Assessment`` fields plus the legacy fields the current workbench
reads (``line_items``, ``marks``, ``damage_summary``, ``files``, ``fixture_notice``,
``finalize_preconditions`` ...), rendered from the stored snapshot and the M8 reason-code
catalogue. Display text always comes from a reason code, never free composition.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..comparison.reason_codes import REASON_CATALOGUE_VERSION, SYNTHETIC_COST_NOTICE, display_text, is_known
from ..contracts.documents import PenMark
from ..documents.pen_marks import row_mark_states
from ..fixtures import FIXTURE_NOTICE
from ..messaging.outbox import backlog
from ..orchestration import state
from ..orchestration.plan import BRANCH_OF, STAGES
from ..persistence.tables import assessments, current_pointer, branch_state, jobs
from ..runtime import get, Record

OPEN_RESULTS = ("unsupported", "cost_outlier", "insufficient_evidence")
_MARK_TEXT = {
    ("price_change", "pending"): "Proposed price-change mark (fixture detector stand-in). Confirm it with the revised "
                                 "amount, or reject it. The printed amount is never used while it is pending.",
    ("exclusion", "pending"): "Proposed exclusion mark (fixture detector stand-in). Confirm it to exclude the row, "
                              "or reject it.",
    ("price_change", "confirmed"): "Price change confirmed by the surveyor.",
    ("exclusion", "confirmed"): "Exclusion confirmed by the surveyor; the row is not checked.",
    ("price_change", "rejected"): "Price-change mark rejected; the printed amount applies.",
    ("exclusion", "rejected"): "Exclusion mark rejected; the row is checked normally.",
}
_UNLINKED_TEXT = "This mark sits between rows. Choose the row it belongs to before confirming it."


def text_for(code: str) -> str:
    return display_text(code) if is_known(code) else code.replace("_", " ").capitalize()


def pointer_row(db: Session, claim_id: str) -> Mapping[str, Any] | None:
    return db.execute(select(current_pointer).where(current_pointer.c.claim_id == claim_id)).mappings().first()


def assessment_row(db: Session, claim_id: str, revision: int) -> Mapping[str, Any] | None:
    return db.execute(select(assessments).where(assessments.c.claim_id == claim_id,
                                                assessments.c.assessment_revision == revision)).mappings().first()


def assessment_rows(db: Session, claim_id: str) -> list[Mapping[str, Any]]:
    return list(db.execute(select(assessments).where(assessments.c.claim_id == claim_id)
                           .order_by(assessments.c.assessment_revision)).mappings())


def _incomplete(db: Session, claim_id: str, revision: int, bs: Mapping[str, Any] | None, job_rows=None) -> list[dict[str, Any]]:
    failed = [{"stage": j["stage"], "job_key": j["job_key"], "reason_code": j["reason_code"], "state": j["state"]}
              for j in (state.revision_jobs(db, claim_id, revision) if job_rows is None else job_rows) if j["state"] in ("failed", "dead_lettered")]
    if bs is not None:
        for stage in STAGES:
            if bs[stage] == "failed" and not any(f["stage"] == stage for f in failed):
                failed.append({"stage": stage, "job_key": None, "reason_code": (bs["reasons"].get(stage) or
                                                                                  ["stage_failed"])[0],
                               "state": "failed"})
    return failed


def review_for(db: Session, claim: Mapping[str, Any], revision: int) -> dict[str, Any]:
    """The stored review of an assessment, or the one it starts with (carried actions, not yet saved)."""
    stored = get(db, f"review:{claim['claim_id']}:{revision}")
    if stored is not None:
        return deepcopy(stored)
    row = assessment_row(db, claim["claim_id"], revision)
    base = None
    if row is not None:
        base_input = get(db, f"input:{claim['claim_id']}:{row['input_revision']}") or {}
        base = base_input.get("base_assessment_revision")
    prior = get(db, f"review:{claim['claim_id']}:{base}") if base else None
    return {"review_revision": claim["review_revision"], "assessment_revision": revision,
            "base_assessment_revision": base, "actions": deepcopy(prior["actions"]) if prior else [],
            "finalized": False}


def claim_view(db: Session, claim: Mapping[str, Any], *, snapshot=None) -> dict[str, Any]:
    """The stored claim plus derived status, current assessment and counts."""
    view = deepcopy(dict(claim))
    cid, rev = claim["claim_id"], claim["input_revision"]
    pointer = pointer_row(db, cid) if snapshot is None else snapshot["pointers"].get(cid)
    current = pointer is not None and pointer["input_revision"] == rev
    bs = (state.branch_row(db, cid, rev) if rev else None) if snapshot is None else snapshot["branches"].get((cid, rev))
    records = snapshot["records"] if snapshot is not None else None
    claim_input = (get(db, f"input:{cid}:{rev}") if rev else None) if records is None else records.get(f"input:{cid}:{rev}")
    view.update(latest_assessment_revision=pointer["assessment_revision"] if pointer else None,
                assessment_revision=pointer["assessment_revision"] if current else None,
                finding_count=pointer["finding_count"] if current else 0,
                estimate_row_count=pointer["estimate_row_count"] if current else 0,
                declared_total=pointer["declared_total"] if current else None,
                photograph_count=len(claim_input.get("photo_ids", [])) if claim_input else 0)
    job_rows = None if snapshot is None else snapshot["jobs"].get((cid, rev), [])
    failed = _incomplete(db, cid, rev, bs, job_rows) if rev else []
    if not rev:
        status, processing = "awaiting_upload", "awaiting_upload"
    elif current:
        review_key = f"review:{cid}:{pointer['assessment_revision']}"
        review = (get(db, review_key) if records is None else records.get(review_key)) or {}
        status = "ready_to_print" if review.get("finalized") else "in_review"
        row = assessment_row(db, cid, pointer["assessment_revision"]) if snapshot is None else snapshot["assessments"].get((cid, pointer["assessment_revision"]))
        documents = bool(claim_input and claim_input.get("page_targets"))
        processing = "ready" if documents else "awaiting_declared_entries"
        if row is not None and row["state"] == "incomplete" and documents:
            processing = "incomplete"
    elif failed:
        status, processing = "incomplete", "incomplete"
    else:
        intake = state.stage_jobs(db, cid, rev, "intake") if job_rows is None else [j for j in job_rows if j["stage"] == "intake"]
        queued = bool(intake) and intake[0]["state"] in ("pending", "dispatched")
        status, processing = "processing", "queued" if queued else "processing"
    view.update(status=status, processing_state=processing)
    return view


def claim_views(db: Session, claims: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Fetch current list metadata in fixed batches, without loading historical assessments."""
    if not claims:
        return []
    ids = [c["claim_id"] for c in claims]
    revisions = [(c["claim_id"], c["input_revision"]) for c in claims if c["input_revision"]]
    pointers = {r["claim_id"]: r for r in db.execute(select(current_pointer).where(
        current_pointer.c.claim_id.in_(ids))).mappings()}
    branches = {(r["claim_id"], r["input_revision"]): r for r in db.execute(select(branch_state).where(
        tuple_(branch_state.c.claim_id, branch_state.c.input_revision).in_(revisions))).mappings()}
    grouped_jobs = {}
    for r in db.execute(select(jobs.c.claim_id, jobs.c.input_revision, jobs.c.job_key, jobs.c.stage,
                              jobs.c.state, jobs.c.reason_code).where(
            tuple_(jobs.c.claim_id, jobs.c.input_revision).in_(revisions))).mappings():
        grouped_jobs.setdefault((r["claim_id"], r["input_revision"]), []).append(r)
    keys = [f"input:{cid}:{rev}" for cid, rev in revisions] + [
        f"review:{cid}:{p['assessment_revision']}" for cid, p in pointers.items()]
    records = {r.key: r.data for r in db.scalars(select(Record).where(Record.key.in_(keys)))}
    assessment_keys = [(cid, p["assessment_revision"]) for cid, p in pointers.items()]
    current_states = {(r.claim_id, r.assessment_revision): {"state": r.state} for r in db.execute(
        select(assessments.c.claim_id, assessments.c.assessment_revision, assessments.c.state).where(
            tuple_(assessments.c.claim_id, assessments.c.assessment_revision).in_(assessment_keys)))}
    snapshot = {"pointers": pointers, "branches": branches, "jobs": grouped_jobs,
                "records": records, "assessments": current_states}
    return [claim_view(db, c, snapshot=snapshot) for c in claims]


def processing_view(db: Session, claim: Mapping[str, Any], input_revision: int | None = None) -> dict[str, Any]:
    cid = claim["claim_id"]
    rev = input_revision or claim["input_revision"]
    view = claim_view(db, claim)
    bs = state.branch_row(db, cid, rev) if rev else None
    jobs = state.revision_jobs(db, cid, rev) if rev else []
    job_items = [{"job_key": j["job_key"], "stage": j["stage"], "task": j["task"], "target": j["target"],
                  "state": j["state"], "attempt_count": j["attempt_count"], "attempt_epoch": j["attempt_epoch"],
                  "reason_code": j["reason_code"], "error": j["reason_code"],
                  "updated_at": j["updated_at"].isoformat() if j["updated_at"] else None,
                  "retryable": j["state"] in ("failed", "dead_lettered") and j["stage"] != "intake"} for j in jobs]
    stages = []
    if bs is not None:
        for stage in STAGES:
            lineage = state.lineage_row(db, cid, rev, stage)
            stages.append({"stage": stage, "branch": BRANCH_OF[stage], "state": bs[stage],
                           "reason_codes": list(bs["reasons"].get(stage, [])),
                           "jobs": sum(1 for j in jobs if j["stage"] == stage),
                           "reused_from_input_revision": lineage["source_input_revision"] if lineage else None})
    unpublished = backlog(db, cid)
    failed = _incomplete(db, cid, rev, bs) if rev else []
    consolidate = next((j for j in reversed(jobs) if j["stage"] == "consolidate"), None)
    return {"claim_id": cid, "input_revision": rev, "state": view["status"], "processing_state": view["processing_state"],
            "branches": state.branch_states(bs) if bs is not None else {},
            "stages": stages, "jobs": job_items,
            "dispatch_pending": unpublished > 0 or any(j["state"] == "pending" for j in jobs),
            "unpublished_messages": unpublished, "incomplete": bool(failed), "failures": failed,
            "dead_lettered": any(j["state"] == "dead_lettered" for j in jobs),
            "consolidate": {"emitted": bool(bs and bs["consolidate_emitted_at"]),
                            "job_key": consolidate["job_key"] if consolidate else None,
                            "state": consolidate["state"] if consolidate else None},
            "awaiting_input_reason": ("no_estimate_pages" if bs is not None and bs["expected_pages"] == 0 else None),
            "assessment_revision": view["assessment_revision"], "fixture_mode": True}


# ------------------------------------------------------------------ assessment view
def _row_legacy(finding: Mapping[str, Any], item: Mapping[str, Any], marks: list[Mapping[str, Any]],
                mark_state: Any, photo_ids: set[str]) -> dict[str, Any]:
    codes = [r["code"] for r in finding["reasons"]]
    evidence = [r["ref_id"] for r in finding["evidence_refs"] if r["kind"] == "photo" and r["ref_id"] in photo_ids]
    cost = finding["cost_check"]
    return {
        "entry_id": item["entry_id"], "finding_id": finding["finding_id"],
        "description": " ".join(t for t in (item["original_part_text"], item["original_operation_text"]) if t),
        "part_code": item["part_code"] or "unmapped", "side": item["side"], "operation": item["operation"] or "unmapped",
        "quantity": item["quantity"], "printed_amount": item["printed_line_amount"],
        "original_printed_amount": item.get("original_printed_line_amount"),
        "printed_amount_corrected": item.get("printed_amount_corrected", False),
        "original_amount_text": item.get("original_amount_text"),
        "effective_amount": item["effective_price"], "effective_price_source": item["effective_price_source"],
        "effective_price_reason": item["effective_price_reason"], "currency": item["currency"],
        "cost_basis": item["cost_basis"], "mark_state": mark_state.state if mark_state else "none",
        "mark_ids": [m["mark_id"] for m in marks], "row_state": finding["row_state"],
        "documentary_check": finding["documentary_check"]["result"], "mark_check": finding["mark_state_check"]["result"],
        "photo_check": finding["photographic_check"]["result"], "cost_check": cost["result"],
        "cost_range": ({"lower": cost["lower_amount"], "upper": cost["upper_amount"],
                        "cost_table_version": cost["cost_table_version"], "range_id": cost["range_id"],
                        "support": cost.get("independent_base_case_count")}
                       if cost.get("lower_amount") is not None else None),
        "overall_result": finding["overall_result"], "reason_code": codes[0] if codes else None,
        "reason_codes": codes, "reason": " ".join(text_for(c) + "." for c in codes[:2]) if codes else "",
        "evidence_ids": evidence, "row_box_norm": item["row_box_norm"], "page_id": item["page_id"]}


def _mark_legacy(mark: Mapping[str, Any]) -> dict[str, Any]:
    text = _UNLINKED_TEXT if mark["entry_id"] is None and mark["state"] == "pending" else \
        _MARK_TEXT[(mark["mark_type"], mark["state"])]
    return {"mark_id": mark["mark_id"], "entry_id": mark["entry_id"], "type": mark["mark_type"],
            "state": mark["state"], "candidate_entry_ids": list(mark["candidate_entry_ids"]),
            "link_reason": mark["link_reason"], "confirmed_amount": mark["confirmed_amount"],
            "detection_confidence": mark["detection_confidence"], "origin": mark["origin"],
            "box_norm": mark["box_norm"], "page_id": mark["page_id"], "reason": text}


def _damage_summary(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    coverage = {(c["part_code"], c["side"]): c for c in inputs.get("coverage", [])}
    listed, rows = set(), []
    for summary in inputs.get("part_summaries", []):
        slot = coverage.get((summary["part_code"], summary["side"])) if summary["part_code"] else None
        state_code = slot["state"] if slot else "unresolved"
        reasons = (slot["reasons"] if slot else []) + list(summary["reasons"])
        rows.append({"summary_id": summary["summary_id"], "part_code": summary["part_code"] or "unresolved",
                     "side": summary["side"], "identity_status": summary["identity_status"], "coverage": state_code,
                     "damage_codes": summary["damage_codes"], "observation_count": summary["observation_count"],
                     "photo_count": len(summary["supporting_photo_ids"]), "reason_codes": reasons,
                     "reason": "Fixture observation; no model analysed the uploaded photographs. Coverage "
                               f"{state_code.replace('_', ' ')}" + (f" ({', '.join(reasons)})." if reasons else ".")})
        listed.add((summary["part_code"], summary["side"]))
    for (part, side), slot in coverage.items():
        if side != "unknown" and (part, side) not in listed:
            rows.append({"summary_id": None, "part_code": part, "side": side, "identity_status": "resolved",
                         "coverage": slot["state"], "damage_codes": [], "observation_count": 0,
                         "photo_count": len(slot["covering_photo_ids"]), "reason_codes": list(slot["reasons"]),
                         "reason": f"No damage observation. Coverage {slot['state'].replace('_', ' ')}."})
    return rows


def preconditions(db: Session, claim_v: Mapping[str, Any], row: Mapping[str, Any],
                  review: Mapping[str, Any]) -> dict[str, Any]:
    from .review_service import gate
    return gate(db, claim_v, row, review)


def assessment_view(db: Session, claim: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    claim_v = claim_view(db, claim)
    body, inputs = deepcopy(row["body"]), row["inputs"]
    cid = claim["claim_id"]
    review = review_for(db, claim, row["assessment_revision"])
    claim_input = get(db, f"input:{cid}:{row['input_revision']}") or {}
    file_ids = claim_input.get("file_ids", [])
    by_id = {r.data["file_id"]: r.data for r in db.scalars(select(Record).where(
        Record.kind == "file", Record.claim_id == cid, Record.key.in_(["file:" + fid for fid in file_ids])))}
    files = [by_id[fid] for fid in file_ids if fid in by_id]
    items = {i["entry_id"]: i for i in inputs.get("line_items", [])}
    marks = inputs.get("pen_marks", [])
    states = row_mark_states(list(items), [PenMark.model_validate(m) for m in marks]) if items else {}
    photo_ids = {f["file_id"] for f in files if f.get("role") == "photograph" and not f.get("fixture_placeholder")}
    line_items = []
    for finding in body["findings"]:
        item = items[finding["entry_id"]]
        row_marks_list = [m for m in marks if m["entry_id"] == finding["entry_id"]
                          or (m["entry_id"] is None and finding["entry_id"] in m["candidate_entry_ids"])]
        line_items.append(_row_legacy(finding, item, row_marks_list, states.get(finding["entry_id"]), photo_ids))
    codes = sorted({r["code"] for f in body["findings"] for r in f["reasons"]} |
                   {r["code"] for r in body["missing_repairs_check"]["reasons"]} | set(body["incomplete_reasons"]))
    declaration = inputs.get("declaration")
    fixture = body["provenance"]["source_kind"] == "fixture"
    view = {**body,
            "is_current": claim_v["assessment_revision"] == row["assessment_revision"],
            "trigger": row["trigger"], "job_key": row["job_key"], "reuse_lineage": row["reuse_lineage"],
            "mark_actions_applied": inputs.get("mark_actions_applied", []),
            "current_review_revision": review["review_revision"],
            "line_items": line_items, "marks": [_mark_legacy(m) for m in marks],
            "damage_summary": _damage_summary(inputs), "part_summaries": inputs.get("part_summaries", []),
            "coverage": inputs.get("coverage", []), "observations": inputs.get("observations", []),
            "pages": inputs.get("pages", []),
            "declaration_completeness": declaration["state"] if declaration else "not_supplied",
            "declaration": declaration,
            "files": [f for f in files if not f.get("fixture_placeholder")],
            "fixture_files": [f for f in files if f.get("fixture_placeholder")],
            "fixture_scenario": inputs.get("fixture_scenario"),
            "invalidated_corrections": claim_input.get("invalidated_corrections", []),
            "versions": {**body["pinned_versions"], "cost_table": row["cost_table_version"],
                         "rules_config": row["rules_config_version"], "schema": body["schema_version"]},
            "reason_catalogue_version": REASON_CATALOGUE_VERSION,
            "reason_texts": {code: text_for(code) for code in codes},
            "cost_notice": SYNTHETIC_COST_NOTICE,
            "fixture_notice": FIXTURE_NOTICE if fixture else None}
    from .review_service import load
    domain = load(db, claim, row, review)
    view["review_overlay"] = {
        "dismissals": {key: {**event.model_dump(mode="json"),
                             "carried_from": event.finding_id if key != event.finding_id else None}
                       for key, event in domain.dismissals().items()},
        "addition_decisions": {key: event.model_dump(mode="json") for key, event in domain.addition_decisions().items()},
        "accepted_scope": [event.model_dump(mode="json") for event in
                           (*domain.accepted_scope, *domain.events) if event.action_type == "accept_addition"]}
    view["review_photo_ids"] = inputs.get("fixture_photo_ids", [])
    view["finalize_preconditions"] = preconditions(db, claim_v, row, review)
    return view


def assessment_summary(claim_v: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    body = row["body"]
    return {"assessment_revision": row["assessment_revision"], "input_revision": row["input_revision"],
            "state": row["state"], "superseded": row["superseded"], "trigger": row["trigger"],
            "is_current": claim_v["assessment_revision"] == row["assessment_revision"],
            "created_at": body["created_at"], "cost_table_version": row["cost_table_version"],
            "rules_config_version": row["rules_config_version"], "pinned_versions": body["pinned_versions"],
            "finding_counts": _counts(body), "provenance": body["provenance"]}


def _counts(body: Mapping[str, Any]) -> dict[str, int]:
    counts = {k: 0 for k in ("ok", "unsupported", "cost_outlier", "insufficient_evidence", "excluded")}
    for f in body["findings"]:
        counts["excluded" if f["row_state"] == "excluded" else f["overall_result"]] += 1
    return counts


__all__ = ["assessment_row", "assessment_rows", "assessment_summary", "assessment_view", "claim_view", "pointer_row",
           "preconditions", "processing_view", "review_for", "text_for"]
