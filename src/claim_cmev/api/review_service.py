"""Persistence adapter for the pure M9 review, finalization and print functions.

Historical baseline reviews remain readable. New actions retain typed events and
request hashes alongside the legacy display history; assessments are never edited.
"""
from copy import deepcopy
from sqlalchemy import select
from ..persistence.tables import assessments
from datetime import datetime

from ..contracts.assessment import Assessment
from ..contracts.claims import ClaimInput
from ..contracts.common import COST_BASIS
from ..contracts.review import ReviewEvent
from ..comparison.lineage import dismissal_carries_forward
from ..contracts.documents import DeclarationCompleteness, LineItem, PenMark
from ..contracts.imaging import PartCoverage, PartSummary
from ..orchestration import state as workflow
from ..orchestration.intake import contract_vehicle_class
from ..review import open_review, evaluate_finalize_preconditions, finalize_review, build_print_payload
from ..review.state import ReviewState
from ..runtime import get, put, utcnow, uid


def load(db, claim, row, review):
    from . import views
    if review.get("domain_state"):
        result = ReviewState.model_validate(review["domain_state"])
    else:
        inputs = row["inputs"]
        result = open_review(
            Assessment.model_validate(row["body"]), currency=claim["currency"], cost_basis=COST_BASIS,
            review_revision=review["review_revision"],
            line_items=[LineItem.model_validate(i) for i in inputs.get("line_items", [])],
            marks=[PenMark.model_validate(m) for m in inputs.get("pen_marks", [])],
            completeness=DeclarationCompleteness.model_validate(inputs["declaration"]) if inputs.get("declaration") else None,
            part_summaries=[PartSummary.model_validate(s) for s in inputs.get("part_summaries", [])],
            coverage=[PartCoverage.model_validate(c) for c in inputs.get("coverage", [])],
            photo_ids=set(inputs.get("fixture_photo_ids", [])),
            page_ids={p["page_id"] for p in inputs.get("pages", [])})
    # Carry original audit events without pretending they were submitted to this assessment.
    inherited = [ReviewEvent.model_validate({k: v for k, v in a.items() if k in ReviewEvent.model_fields})
                 for a in review.get("actions", [])
                 if a.get("action_type") and a.get("assessment_revision") != row["assessment_revision"]]
    source_revisions = {e.assessment_revision for e in inherited
                        if e.action_type in ("dismiss_finding", "accept_addition", "dismiss_addition")}
    sources = {r.assessment_revision: Assessment.model_validate(r.body) for r in db.execute(
        select(assessments.c.assessment_revision, assessments.c.body).where(
            assessments.c.claim_id == claim["claim_id"],
            assessments.c.assessment_revision.in_(source_revisions)))} if source_revisions else {}
    notes, dismissals, additions, accepted = [], {}, {}, []
    for event in inherited:
        if event.action_type == "add_note":
            notes.append(event)
        if event.action_type not in ("dismiss_finding", "accept_addition", "dismiss_addition"):
            continue
        source = sources.get(event.assessment_revision)
        if source is None:
            continue
        if event.action_type == "dismiss_finding":
            old = next((f for f in source.findings if f.finding_id == event.finding_id), None)
            for current in result.assessment.findings:
                if old and dismissal_carries_forward(old, current):
                    dismissals[current.finding_id] = event
        else:
            old = next((c for c in source.possible_additions if c.candidate_id == event.candidate_id), None)
            if event.action_type == "accept_addition":
                accepted.append(event)
            for current in result.assessment.possible_additions:
                if old and (old.part_code, old.side, old.status, sorted(old.observation_ids)) == (
                        current.part_code, current.side, current.status, sorted(current.observation_ids)):
                    additions[current.candidate_id] = event
    return result.model_copy(update={
        "inherited_notes": tuple(notes), "inherited_dismissals": dismissals,
        "inherited_addition_decisions": additions, "accepted_scope": tuple(accepted),
        "current_input_revision": claim["input_revision"],
        "claim_assessment_revision": views.claim_view(db, claim)["assessment_revision"]})


def stages(db, claim):
    bs = workflow.branch_row(db, claim["claim_id"], claim["input_revision"])
    return {s: bs[s] for s in ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")} if bs else {}


def gate(db, claim, row, review):
    result = evaluate_finalize_preconditions(
        load(db, claim, row, review), presented_review_revision=claim["review_revision"], stage_states=stages(db, claim))
    return {**result.model_dump(mode="json"), "can_finalize": result.allowed and not review.get("finalized"),
            "checks": [{"code": c.precondition, "passed": c.passed,
                        "message": " ".join(b.message for b in c.blockers) or c.precondition.replace("_", " ")}
                       for c in result.conditions if c.enforced]}


def save_action(db, claim, row, review, outcome):
    event = outcome.event
    review["domain_state"] = outcome.state.model_dump(mode="json")
    review["review_revision"] = outcome.review_revision
    display = event.model_dump(mode="json")
    display.update(type="note" if event.action_type == "add_note" else event.action_type,
                   text=event.note, created_at=event.recorded_at.isoformat())
    review["actions"].append(display)
    claim["review_revision"] = outcome.review_revision
    put(db, f"review:{claim['claim_id']}:{row['assessment_revision']}", "review", review, claim["claim_id"])
    put(db, "claim:" + claim["claim_id"], "claim", claim, claim["claim_id"])


def freeze(db, claim, row, review, *, actor, expected):
    from . import views
    result = finalize_review(load(db, claim, row, review), presented_review_revision=expected,
                             stage_states=stages(db, claim), actor=actor, finalized_at=utcnow(),
                             finalization_id="fin-" + uid().lower())
    if result.http_status != 200:
        return result, None
    review.update(domain_state=result.state.model_dump(mode="json"),
                  review_revision=result.state.review_revision, finalized=True,
                  finalized_at=result.finalization.finalized_at.isoformat())
    review["claim_snapshot"] = views.claim_view(db, claim)
    snapshot = views.assessment_view(db, claim, row)
    snapshot.pop("finalize_preconditions", None)
    review["assessment_snapshot"] = snapshot
    inp = get(db, f"input:{claim['claim_id']}:{row['input_revision']}")
    vehicle = claim.get("vehicle", {})
    klass = contract_vehicle_class(vehicle.get("vehicle_class"))
    contract = ClaimInput(
        claim_id=claim["claim_id"], input_revision=row["input_revision"],
        external_reference=claim.get("reference"), external_reference_reason=None if claim.get("reference") else "not_supplied",
        make=vehicle.get("make") or "unknown", model=vehicle.get("model") or "unknown", year=vehicle.get("year"),
        vehicle_class=klass, vehicle_class_reason="not_supplied" if klass == "unknown" else None,
        currency=claim["currency"], cost_basis=COST_BASIS, file_ids=inp["file_ids"],
        created_at=datetime.fromisoformat(inp["created_at"]), previous_input_revision=inp["previous_input_revision"],
        provenance=row["body"]["provenance"])
    report = build_print_payload(contract, result.state.assessment, result.state).model_dump(mode="json")
    report["review_history"] = deepcopy(review["actions"])
    review["report"] = report
    put(db, f"review:{claim['claim_id']}:{row['assessment_revision']}", "review", review, claim["claim_id"])
    return result, review


def public(review):
    return {k: deepcopy(v) for k, v in review.items()
            if k not in ("domain_state", "claim_snapshot", "assessment_snapshot", "report")}
