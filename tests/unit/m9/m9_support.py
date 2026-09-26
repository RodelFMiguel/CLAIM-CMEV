"""Small, valid M9 review scenarios built from the shared contracts. Offline and deterministic.

Two assessments over one claim:

* ``review``: the state a surveyor opens. ``pm1`` is a pending price change on ``li2``,
  ``pm2`` a pending exclusion on ``li3``, ``pm4`` a confirmed price change on ``li4``.
* ``clean``: every mark decided, so F1 to F6 pass; ``li2`` and ``li5`` stay
  ``insufficient_evidence`` and ``li3`` is a confirmed exclusion.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any

from claim_cmev.contracts import (
    Assessment,
    AssessmentFinding,
    ClaimInput,
    DeclarationCompleteness,
    LineItem,
    PartCoverage,
    PartSummary,
    PenMark,
    ProposedRepairAddition,
    ReusedArtifact,
)
from claim_cmev.review import ReviewActionRequest, open_review

CLAIM = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
BASIS = "single_part_pre_tax_no_discount_v1"
NOW = datetime(2026, 9, 24, 2, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 24, 2, 30, tzinfo=UTC)
PINNED = {"parts_model": "segformer-parts-0.3.1", "damage_model": "segformer-damage-0.2.4",
          "penmark_model": "frcnn-penmarks-0.2.0", "ocr": "paddleocr-2.7.0", "parser_config": "parser-0.4.2",
          "rules_config": "rc-0.1.0", "cost_table": "2026.09.1"}
ALL_STAGES_DONE = {"parts": "succeeded", "damage": "succeeded", "summary": "succeeded", "page_read": "succeeded",
                   "line_items": "succeeded", "pen_marks": "succeeded"}


def prov(kind: str = "fixture") -> dict:
    return {"source_kind": kind, "runtime_profile": "lean", "producer_service": "tests-m9"}


def scope(kind: str = "fixture", input_revision: int = 1) -> dict:
    return {"claim_id": CLAIM, "input_revision": input_revision, "provenance": prov(kind)}


def row_box(index: int) -> tuple[float, float, float, float]:
    top = 0.20 + 0.10 * index
    return (0.10, top, 0.90, top + 0.06)


def line_item(entry_id: str, index: int, part: str, operation: str, printed: str | None, *, kind: str = "fixture",
              effective: str | None = "printed", side: str = "not_applicable", **kw: Any) -> LineItem:
    data = {**scope(kind), "versions": {"parser_config": "parser-0.4.2"}, "entry_id": entry_id, "page_id": "dp1",
            "page_number": 1, "row_box_norm": row_box(index), "amount_box_norm": (0.75, row_box(index)[1], 0.88,
                                                                                 row_box(index)[3]),
            "original_part_text": part.upper(), "original_operation_text": operation.upper(), "part_code": part,
            "part_mapping_status": "resolved", "side": side,
            "side_source": "absent" if side == "unknown" else "document_text", "operation": operation,
            "operation_mapping_status": "resolved", "quantity": "1", "unit_price": printed,
            "printed_line_amount": printed, "currency": "SGD", "cost_basis": BASIS, "field_uncertainty": []}
    if effective == "printed":
        data.update(effective_price=printed, effective_price_source="printed")
    elif effective is None:
        data.update(effective_price=None, effective_price_source="unresolved",
                    effective_price_reason="price_change_pending")
    else:
        data.update(effective_price=effective, effective_price_source="surveyor_entry")
    data.update(kw)
    return LineItem.model_validate(data)


def mark(mark_id: str, entry_id: str | None, mark_type: str, state: str = "pending", *, kind: str = "fixture",
         amount: str | None = None, candidates: list[str] | None = None, index: int = 0, **kw: Any) -> PenMark:
    top = row_box(index)[1]
    data = {**scope(kind), "versions": {"penmark_model": "frcnn-penmarks-0.2.0"}, "mark_id": mark_id,
            "page_id": "dp1", "box_norm": (0.76, top + 0.005, 0.87, top + 0.05), "mark_type": mark_type,
            "detection_confidence": 0.81, "entry_id": entry_id,
            "candidate_entry_ids": candidates if candidates is not None else ([entry_id] if entry_id else []),
            "link_reason": "unambiguous_row_overlap" if entry_id else "mark_between_rows", "state": state,
            "origin": "detector"}
    if state != "pending":
        data.update(decision_action_id=f"act-{mark_id}", decided_by="surveyor:rm", decided_at=NOW, review_revision=1)
    if amount is not None:
        data.update(confirmed_amount=amount, confirmed_currency="SGD", confirmed_cost_basis=BASIS)
    data.update(kw)
    return PenMark.model_validate(data)


def reason(code: str, message: str | None = None) -> dict:
    return {"code": code, "message": message or f"text for {code}"}


def check(result: str, code: str) -> dict:
    return {"result": result, "reasons": [reason(code)]}


def cost(entry_id: str, result: str = "not_evaluated", code: str = "check_not_reached", **kw: Any) -> dict:
    return {"entry_id": entry_id, "result": result, "reason_code": code, "policy_version": "rc-0.1.0",
            "cost_table_version": "2026.09.1", **kw}


def finding(finding_id: str, entry_id: str, overall: str, *, revision: int = 1, content_hash: str | None = None,
            reasons: list[dict] | None = None) -> AssessmentFinding:
    base = {"finding_id": finding_id, "assessment_revision": revision, "entry_id": entry_id,
            "evidence_refs": [{"kind": "line_item", "ref_id": entry_id}], "pinned_versions": PINNED,
            "created_at": NOW, "content_hash": content_hash or hashlib.sha256(finding_id.encode()).hexdigest()}
    if overall == "ok":
        base.update(documentary_check=check("passed", "row_fields_mapped"), mark_state_check=check("passed", "no_marks"),
                    photographic_check=check("passed", "damage_supported"),
                    cost_check=cost(entry_id, "within_range", "amount_in_range", amount="980.00",
                                    lower_amount="620.00", upper_amount="1020.00", range_id="r-front-bumper-replace",
                                    absolute_deviation="0.00", independent_base_case_count=47),
                    applied_range_id="r-front-bumper-replace", reasons=[reason("amount_in_range")])
    elif overall == "cost_outlier":
        base.update(documentary_check=check("passed", "row_fields_mapped"), mark_state_check=check("passed", "no_marks"),
                    photographic_check=check("passed", "damage_supported"),
                    cost_check=cost(entry_id, "outside_range", "amount_above_range", amount="610.00",
                                    lower_amount="240.00", upper_amount="520.00", range_id="r-headlight-replace",
                                    direction="above", absolute_deviation="90.00", independent_base_case_count=38),
                    applied_range_id="r-headlight-replace", reasons=[reason("amount_above_range")])
    elif overall == "not_evaluated":
        ne = check("not_evaluated", "exclusion_confirmed")
        base.update(documentary_check=ne, mark_state_check=ne, photographic_check=ne,
                    cost_check=cost(entry_id, code="exclusion_confirmed"), applied_range_id=None,
                    applied_range_reason="exclusion_confirmed", row_state="excluded",
                    reasons=[reason("exclusion_confirmed", "Excluded by the surveyor, not checked")])
    else:  # insufficient_evidence, withheld at a named rule
        code = (reasons or [reason("coverage_inadequate")])[0]["code"]
        base.update(documentary_check=check("passed", "row_fields_mapped"),
                    mark_state_check=check("passed", "no_marks"), photographic_check=check("insufficient", code),
                    cost_check=cost(entry_id), applied_range_id=None, applied_range_reason="check_not_reached",
                    reasons=reasons or [reason(code, "Take more pictures of this part")])
    base.setdefault("row_state", "active")
    return AssessmentFinding.model_validate({**base, "overall_result": overall})


def assessment(findings: list[AssessmentFinding], *, revision: int = 1, input_revision: int = 1,
               kind: str = "fixture", **kw: Any) -> Assessment:
    additions = [
        ProposedRepairAddition(candidate_id="cand1", assessment_revision=revision, summary_ids=["sum-grille"],
                               part_code="grille", side="not_applicable", status="proposed",
                               reason=reason("addition_proposed", "Damage with no matching estimate row")),
        ProposedRepairAddition(candidate_id="cand2", assessment_revision=revision, observation_ids=["ob9"],
                               part_code=None, side="unknown", status="withheld",
                               reason=reason("addition_withheld_identity_unresolved",
                                             "Damage found, part not identified")),
    ]
    data = {"claim_id": CLAIM, "input_revision": input_revision, "assessment_revision": revision, "state": "ready",
            "findings": findings, "missing_repairs_check": check("passed", "addition_proposed"),
            "possible_additions": additions, "suppressed_additions": [], "incomplete_reasons": [],
            "cost_table_version": "2026.09.1", "rules_config_version": "rc-0.1.0", "pinned_versions": PINNED,
            "provenance": prov(kind), "created_at": NOW}
    data.update(kw)
    return Assessment.model_validate(data)


def claim_input(kind: str = "fixture", input_revision: int = 1) -> ClaimInput:
    return ClaimInput.model_validate({
        **scope(kind, input_revision), "external_reference": "CLM-2026-0142", "make": "Toyota",
        "model": "Corolla Altis", "year": 2019, "vehicle_class": "sedan_standard", "currency": "SGD",
        "cost_basis": BASIS, "file_ids": ["ph1", "ph2", "ph3", "dp1"], "created_at": NOW,
        "previous_input_revision": None if input_revision == 1 else input_revision - 1})


def summaries(kind: str = "fixture") -> list[PartSummary]:
    common = {**scope(kind), "versions": {"summary_config": "s-1"}}
    return [
        PartSummary.model_validate({**common, "summary_id": "sum-bumper", "identity_status": "resolved",
                                    "part_code": "front-bumper", "side": "not_applicable",
                                    "member_observation_ids": ["ob1", "ob2"], "observation_count": 2,
                                    "damage_codes": ["dent", "scratch"], "supporting_photo_ids": ["ph1", "ph2"],
                                    "representative_area_fraction": 0.04, "representative_observation_id": "ob1",
                                    "max_confidence": 0.88, "identity_confirmation_ids": ["ic1"]}),
        PartSummary.model_validate({**common, "summary_id": "sum-unres", "identity_status": "unresolved",
                                    "part_code": None, "side": "unknown", "member_observation_ids": ["ob9"],
                                    "observation_count": 1, "damage_codes": ["dent"], "supporting_photo_ids": ["ph3"],
                                    "representative_area_fraction": 0.01, "representative_observation_id": "ob9",
                                    "max_confidence": 0.55}),
    ]


def coverage(kind: str = "fixture") -> list[PartCoverage]:
    common = {**scope(kind), "versions": {"summary_config": "s-1"}}
    return [
        PartCoverage.model_validate({**common, "coverage_id": "cov-bumper", "part_code": "front-bumper",
                                     "side": "not_applicable", "state": "adequate", "covering_photo_ids": ["ph1", "ph2"],
                                     "coverage_confirmation_id": "cc1"}),
        PartCoverage.model_validate({**common, "coverage_id": "cov-fender-l", "part_code": "fender", "side": "left",
                                     "state": "inadequate", "covering_photo_ids": ["ph2"],
                                     "reasons": ["coverage_inadequate"]}),
    ]


def completeness(state: str = "complete", *, kind: str = "fixture", **kw: Any) -> DeclarationCompleteness:
    data = {**scope(kind), "versions": {"parser_config": "parser-0.4.2"}, "state": state,
            "reasons": [] if state == "complete" else ["rows_unparsed"], "unparsed_region_count": 0,
            "layout_family": "family-a-ruled-grid", "source": "parser"}
    data.update(kw)
    return DeclarationCompleteness.model_validate(data)


def artifacts() -> dict[str, tuple[ReusedArtifact, ...]]:
    return {stage: (ReusedArtifact(artifact_id=f"art-{stage}", kind=stage, source_input_revision=1,
                                   producing_job_key=f"{CLAIM}:1:{stage}:all:abcd1234"),)
            for stage in ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")}


def line_items(scenario: str, kind: str = "fixture") -> list[LineItem]:
    li2_effective = None if scenario == "review" else "300.00"
    return [
        line_item("li1", 0, "front-bumper", "replace", "980.00", kind=kind),
        line_item("li2", 1, "hood", "repair", "340.00", kind=kind, effective=li2_effective),
        line_item("li3", 2, "back-door", "repair", "480.00", kind=kind, side="left"),
        line_item("li4", 3, "headlight", "replace", "650.00", kind=kind, effective="610.00", side="left"),
        line_item("li5", 4, "fender", "repair", "265.00", kind=kind, side="left"),
    ]


def marks(scenario: str, kind: str = "fixture") -> list[PenMark]:
    if scenario == "review":
        first = [mark("pm1", "li2", "price_change", kind=kind, index=1),
                 mark("pm2", "li3", "exclusion", kind=kind, index=2)]
    else:
        first = [mark("pm1", "li2", "price_change", "confirmed", kind=kind, amount="300.00", index=1),
                 mark("pm2", "li3", "exclusion", "confirmed", kind=kind, index=2)]
    return first + [mark("pm4", "li4", "price_change", "confirmed", kind=kind, amount="610.00", index=3)]


def findings(scenario: str) -> list[AssessmentFinding]:
    li2 = [reason("mark_pending", "Confirm the pen mark on this row")] if scenario == "review" else \
        [reason("no_key", "No reference range for this combination")]
    li3 = finding("f3", "li3", "insufficient_evidence", reasons=[reason("mark_pending")]) if scenario == "review" \
        else finding("f3", "li3", "not_evaluated")
    return [finding("f1", "li1", "ok"), finding("f2", "li2", "insufficient_evidence", reasons=li2), li3,
            finding("f4", "li4", "cost_outlier"), finding("f5", "li5", "insufficient_evidence")]


def state(scenario: str = "review", *, kind: str = "fixture", review_revision: int = 0, **kw: Any):
    """An opened review; ``kw`` overrides ``open_review`` arguments (e.g. ``assessment=``)."""
    args = dict(currency="SGD", cost_basis=BASIS, review_revision=review_revision,
                line_items=line_items(scenario, kind), marks=marks(scenario, kind),
                completeness=completeness(kind=kind), part_summaries=summaries(kind), coverage=coverage(kind),
                photo_ids={"ph1", "ph2", "ph3"}, page_ids={"dp1"}, base_stage_artifacts=artifacts())
    target = kw.pop("assessment", None) or assessment(findings(scenario), kind=kind)
    args.update(kw)
    return open_review(target, **args)


def request(action_type: str, expected: int = 0, **fields: Any) -> ReviewActionRequest:
    return ReviewActionRequest(action_type=action_type, expected_review_revision=expected, **fields)


def apply(review_state, req, n: int = 1, *, key: str | None = None, config=None):
    from claim_cmev.review import apply_review_action

    return apply_review_action(review_state, req, actor="surveyor:rm", recorded_at=LATER,
                               idempotency_key=key or f"idem-key-{n:04d}", action_id=f"act-{n:04d}", config=config)
