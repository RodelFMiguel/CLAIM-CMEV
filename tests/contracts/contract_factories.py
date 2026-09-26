"""Small valid-record factories for the contract tests. Override any field with kwargs."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib

CLAIM = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
NOW = datetime(2026, 9, 22, 3, 14, 7, tzinfo=UTC)
PROV = {"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "tests"}
SCOPE = {"claim_id": CLAIM, "input_revision": 1, "provenance": PROV}
BASIS = "single_part_pre_tax_no_discount_v1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def artifact(name: str = "a1", media_type: str = "image/png") -> dict:
    return {"artifact_id": name, "object_uri": f"s3://cmev-derived/t/{name}", "sha256": sha(name),
            "media_type": media_type, "byte_count": 10}


def mask(name: str = "m1", photo: str = "ph1", **kw) -> dict:
    return {"artifact_id": name, "object_uri": f"s3://cmev-derived/t/{name}.png", "sha256": sha(name), "width": 512,
            "height": 512, "encoding": "class_index_png", "source_photo_id": photo, **kw}


def transform() -> dict:
    return {"stored_width": 4032, "stored_height": 3024, "model_width": 512, "model_height": 512,
            "scale": 512 / 4032, "pad_left": 0.0, "pad_top": 64.0}


def line_item(**kw) -> dict:
    data = {**SCOPE, "versions": {"parser_config": "p-1"}, "entry_id": "li1", "page_id": "dp1", "page_number": 1,
            "row_box_norm": (0.08, 0.318, 0.95, 0.346), "amount_box_norm": (0.82, 0.32, 0.93, 0.344),
            "original_part_text": "FRT BUMPER", "original_operation_text": "REPLACE", "part_code": "front-bumper",
            "part_mapping_status": "resolved", "side": "not_applicable", "side_source": "document_text",
            "operation": "replace", "operation_mapping_status": "resolved", "quantity": "1", "unit_price": "980.00",
            "printed_line_amount": "980.00", "effective_price": "980.00", "effective_price_source": "printed",
            "currency": "SGD", "cost_basis": BASIS, "field_uncertainty": []}
    data.update(kw)
    return data


def pen_mark(**kw) -> dict:
    data = {**SCOPE, "versions": {"penmark_model": "d-1"}, "mark_id": "pm1", "page_id": "dp1",
            "box_norm": (0.79, 0.306, 0.93, 0.325), "mark_type": "price_change", "detection_confidence": 0.71,
            "entry_id": "li1", "candidate_entry_ids": ["li1"], "link_reason": "unambiguous_row_overlap",
            "state": "pending", "origin": "detector"}
    data.update(kw)
    return data


def decided(**kw) -> dict:
    return {"decision_action_id": "ra1", "decided_by": "surveyor:t", "decided_at": NOW, "review_revision": 2, **kw}


def completeness(**kw) -> dict:
    data = {**SCOPE, "versions": {"parser_config": "p-1"}, "state": "complete", "reasons": [],
            "unparsed_region_count": 0, "layout_family": "family-a-ruled-grid", "source": "parser"}
    data.update(kw)
    return data


def observation(**kw) -> dict:
    data = {**SCOPE, "versions": {"taxonomy": "damage-cardd-1.0.0", "damage_model": "d-1"}, "observation_id": "ob1",
            "photo_id": "ph1", "damage_code": "dent", "damage_confidence": 0.83, "assignment_status": "assigned",
            "part_code": "front-bumper", "part_reason": None,
            "candidates": [{"part_code": "front-bumper", "containment": 0.91, "rank": 1},
                           {"part_code": "grille", "containment": 0.06, "rank": 2}],
            "primary_containment": 0.91, "runner_up_containment": 0.06, "background_containment": 0.03,
            "area_pixels": 5412, "area_fraction": 0.0206, "area_denominator_pixels": 262144,
            "damage_mask_ref": mask("dm1", component_index=1), "part_mask_ref": mask("pm1"),
            "bbox_norm": (0.312, 0.5504, 0.4871, 0.6693), "assignment_config_version": "assign-1.0.0"}
    data.update(kw)
    return data


def coverage(**kw) -> dict:
    data = {**SCOPE, "versions": {"summary_config": "s-1"}, "coverage_id": "pc1", "part_code": "front-door",
            "side": "left", "state": "adequate", "covering_photo_ids": ["ph1"], "coverage_confirmation_id": "cc1"}
    data.update(kw)
    return data


def cost_range(**kw) -> dict:
    data = {"table_version": "2026.09.1", "range_id": "r-front-bumper-replace", "part_code": "front-bumper",
            "operation": "replace", "vehicle_class": "sedan_standard", "currency": "SGD",
            "cost_basis": BASIS, "support_status": "supported", "lower_amount": "620.00", "upper_amount": "1020.00",
            "independent_base_case_count": 47, "record_count": 141, "nominal_coverage": "0.90",
            "as_of_date": "2026-09-22", "cutoff_date": "2026-06-30", "method": "empirical_percentile",
            "synthetic": True, "provenance": {"source_kind": "synthetic", "generator_version": "gen-2026.09.1",
                                             "seed": 20260922}}
    data.update(kw)
    return data


def cost_check(**kw) -> dict:
    data = {"entry_id": "li1", "result": "within_range", "amount": "980.00", "lower_amount": "620.00",
            "upper_amount": "1020.00", "range_id": "r-front-bumper-replace", "direction": None,
            "absolute_deviation": "0.00", "normalised_score": "0.0000", "independent_base_case_count": 47,
            "reason_code": "amount_in_range", "policy_version": "rc-0.1.0", "cost_table_version": "2026.09.1"}
    data.update(kw)
    return data


def check(result: str = "passed", code: str = "damage_supported") -> dict:
    return {"result": result, "reasons": [{"code": code, "message": "text"}]}


def not_evaluated_cost(**kw) -> dict:
    return {"entry_id": "li1", "result": "not_evaluated", "reason_code": "check_not_reached",
            "policy_version": "rc-0.1.0", "cost_table_version": "2026.09.1", **kw}


def finding(**kw) -> dict:
    data = {"finding_id": "f1", "assessment_revision": 1, "entry_id": "li1", "documentary_check": check(code="mapped"),
            "photographic_check": check(), "cost_check": cost_check(), "mark_state_check": check(code="no_marks"),
            "overall_result": "ok", "row_state": "active", "reasons": [{"code": "amount_in_range", "message": "ok"}],
            "evidence_refs": [{"kind": "photo", "ref_id": "ph1"}], "applied_range_id": "r-front-bumper-replace",
            "pinned_versions": {"rules_config": "rc-0.1.0"}, "created_at": NOW}
    data.update(kw)
    return data


def excluded_finding(**kw) -> dict:
    ne = check("not_evaluated", "exclusion_confirmed")
    data = dict(documentary_check=ne, photographic_check=ne, mark_state_check=ne,
                cost_check=not_evaluated_cost(entry_id=kw.get("entry_id", "li1"), reason_code="exclusion_confirmed"),
                overall_result="not_evaluated", row_state="excluded", applied_range_id=None,
                applied_range_reason="exclusion_confirmed",
                reasons=[{"code": "exclusion_confirmed", "message": "Excluded by the surveyor, not checked"}])
    data.update(kw)
    return finding(**data)
