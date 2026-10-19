"""Replay validated human corrections over copies of immutable branch records."""
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts.documents import LineItem, DeclarationCompleteness, with_effective_price
from ..contracts.review import ReviewEvent
from ..documents.line_items import confirm_declaration_completeness
from ..documents.pen_marks import MARK_ACTION_TYPES, apply_review_event
from ..contracts.common import Provenance


def review_events(claim_input):
    return [ReviewEvent.model_validate(c["event"]) for c in claim_input.get("corrections", [])
            if c.get("kind") == "review_event"]


def _unsided_parts():
    from ..taxonomy import load_parts
    return {p["code"] for p in load_parts().meta["parts"] if p.get("sided") is False}


def correct_line_item(item, values, *, input_revision=None, action_id=None):
    """One validated ``correct_line_item`` event applied to a copy of ``item`` (pure).

    ``unknown`` for a part or operation that is already unresolved changes nothing: the
    parser's mapping status and uncertainty reason stay. A side is never inferred: when a
    part-only correction moves a row from an unsided part to a sided one, a side without a
    printed or human source becomes ``unknown`` (source ``absent``), which M8 withholds.
    Raises the ``LineItem`` validation error for a value set that cannot form a valid row.
    """
    values = dict(values)
    for name, field in (("part_code", "part_code"), ("operation", "operation")):
        if values.get(name) == "unknown" and getattr(item, field) is None:
            values.pop(name)  # unchanged: keep the parser's status and reason
    data = item.model_dump()
    data.update(values)
    if input_revision is not None:
        data["input_revision"] = input_revision
    if action_id is not None:
        data.update(lineage=item.entry_id, provenance=item.provenance.model_copy(update={"derivation_refs":
            [*item.provenance.derivation_refs, action_id]}))
    if "part_code" in values:
        data["part_code"] = None if values["part_code"] == "unknown" else values["part_code"]
        data["part_mapping_status"] = "resolved" if data["part_code"] else "unmapped"
    if "operation" in values:
        data["operation"] = None if values["operation"] == "unknown" else values["operation"]
        data["operation_mapping_status"] = "resolved" if data["operation"] else "unmapped"
    if "side" in values:
        data["side_source"] = "absent" if values["side"] == "unknown" else "human_correction"
    elif (data["side"] == "not_applicable" and data["side_source"] == "absent"
          and data["part_code"] not in _unsided_parts()):
        data["side"] = "unknown"  # never inferred; R5 withholds the unknown side
    data["field_uncertainty"] = [u for u in data["field_uncertainty"] if u["field"] not in values]
    if data["part_code"] is None and not any(u["field"] == "part_code" for u in data["field_uncertainty"]):
        data["field_uncertainty"].append({"field": "part_code", "reason": "human_unresolved"})
    if data["operation"] is None and not any(u["field"] == "operation" for u in data["field_uncertainty"]):
        data["field_uncertainty"].append({"field": "operation", "reason": "human_unresolved"})
    if "printed_line_amount" in values:
        if not item.printed_amount_corrected:
            data["original_printed_line_amount"] = item.printed_line_amount
        data["printed_amount_corrected"] = True
        data.update(effective_price=values["printed_line_amount"], effective_price_source="printed",
                    effective_price_reason=None)
    return LineItem.model_validate(data)


def confirm_completeness(current, event, *, items, revision, provenance):
    """A surveyor's completeness confirmation as a new human record (M5 semantics).

    ``explicitly_empty`` goes through M5's ``confirm_declaration_completeness``; the other
    states keep the same rules: a new ``human_confirmation`` record that preserves the
    parser's layout, covered pages and ``unparsed_region_count``.
    """
    values = event.new_values
    reasons = list(values.get("reasons") or [])
    if values["state"] == "explicitly_empty":
        return confirm_declaration_completeness(
            current, remaining_entry_ids=[i.entry_id for i in items], confirmed_by=event.actor,
            confirmed_at=event.recorded_at, review_revision=event.resulting_review_revision,
            input_revision=revision, provenance=provenance,
            **({"reason_code": reasons[0]} if reasons else {}))
    data = current.model_dump()
    data.update(state=values["state"], reasons=reasons, source="human_confirmation", input_revision=revision,
                confirmed_by=event.actor, confirmed_at=event.recorded_at,
                review_revision=event.resulting_review_revision, provenance=provenance)
    return DeclarationCompleteness.model_validate(data)


def _find_old_line_items(session, claim_id: str, old_ids: set[str]) -> dict[str, dict]:
    if not session or not claim_id or not old_ids:
        return {}
    from ..persistence.tables import stage_records
    from sqlalchemy import select
    stmt = select(stage_records.c.record_id, stage_records.c.body).where(
        stage_records.c.claim_id == claim_id,
        stage_records.c.record_kind == "line_item",
        stage_records.c.record_id.in_(old_ids)
    )
    rows = session.execute(stmt).all()
    return {r[0]: (r[1] if isinstance(r[1], dict) else r[1].model_dump()) for r in rows}


def _match_line_item(old_data: dict, current_items: Sequence[LineItem]) -> LineItem | None:
    if not current_items:
        return None
    old_page = old_data.get("page_id")
    page_items = [i for i in current_items if i.page_id == old_page] if old_page else []
    candidates = page_items or list(current_items)

    old_part = old_data.get("original_part_text")
    old_amount = old_data.get("printed_line_amount")

    # 1. Exact match on raw part text
    if old_part:
        exact = [c for c in candidates if c.original_part_text == old_part]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            by_amount = [c for c in exact if str(c.printed_line_amount) == str(old_amount)]
            if len(by_amount) == 1:
                return by_amount[0]
            candidates = exact

    # 2. Box IoU / vertical overlap
    old_box = old_data.get("row_box_norm")
    if old_box and len(old_box) == 4:
        oymin, oxmin, oymax, oxmax = old_box
        best_iou = 0.0
        best_cand = None
        for c in candidates:
            cymin, cxmin, cymax, cxmax = c.row_box_norm
            iy1 = max(oymin, cymin)
            ix1 = max(oxmin, cxmin)
            iy2 = min(oymax, cymax)
            ix2 = min(oxmax, cxmax)
            inter = max(0.0, iy2 - iy1) * max(0.0, ix2 - ix1)
            area_old = max(0.0, oymax - oymin) * max(0.0, oxmax - oxmin)
            area_cand = max(0.0, cymax - cymin) * max(0.0, cxmax - cxmin)
            union = area_old + area_cand - inter
            iou = inter / union if union > 0 else 0.0
            if iou > best_iou:
                best_iou = iou
                best_cand = c
        if best_iou >= 0.3:
            return best_cand

        # Vertical band overlap
        best_y_iou = 0.0
        best_y_cand = None
        for c in candidates:
            cy1, cy2 = c.row_box_norm[0], c.row_box_norm[2]
            inter = max(0.0, min(oymax, cy2) - max(oymin, cy1))
            union = max(oymax, cy2) - min(oymin, cy1)
            y_iou = inter / union if union > 0 else 0.0
            if y_iou > best_y_iou:
                best_y_iou = y_iou
                best_y_cand = c
        if best_y_iou >= 0.4:
            return best_y_cand

    # 3. Match by part_code and amount
    part_matches = [c for c in candidates if c.part_code == old_data.get("part_code") and str(c.printed_line_amount) == str(old_amount)]
    if len(part_matches) == 1:
        return part_matches[0]

    return candidates[0] if candidates else None


def map_line_item_ids(session, claim_id: str | None, current_items: Sequence[LineItem],
                       claim_input: Mapping[str, Any]) -> dict[str, str]:
    if not current_items:
        return {}
    current_ids = {i.entry_id for i in current_items}
    referenced_ids: set[str] = set()
    for c in claim_input.get("corrections", []):
        if c.get("kind") == "mark_action":
            tid = c.get("target_entry_id")
            if tid:
                referenced_ids.add(tid)
        elif c.get("kind") == "review_event":
            ev = c.get("event") or {}
            eid = ev.get("entry_id")
            if eid:
                referenced_ids.add(eid)
            nv = ev.get("new_values") or {}
            if nv.get("entry_id"):
                referenced_ids.add(nv["entry_id"])
            if nv.get("target_entry_id"):
                referenced_ids.add(nv["target_entry_id"])

    missing_ids = referenced_ids - current_ids
    mapping = {eid: eid for eid in current_ids}
    if not missing_ids or not session or not claim_id:
        return mapping

    old_items_by_id = _find_old_line_items(session, claim_id, missing_ids)
    for old_id in missing_ids:
        old_data = old_items_by_id.get(old_id)
        if not old_data:
            continue
        matched_item = _match_line_item(old_data, current_items)
        if matched_item:
            mapping[old_id] = matched_item.entry_id

    return mapping


def apply_corrections(records, marks, claim_input, revision, *, entry_map=None, session=None):
    items = list(records.get("line_item", []))
    declarations = list(records.get("declaration_completeness", []))
    if entry_map is None:
        claim_id = claim_input.get("claim_id") or (items[0].claim_id if items else None)
        entry_map = map_line_item_ids(session, claim_id, items, claim_input)
    provenance = Provenance(source_kind="real", runtime_profile=(items[0].provenance.runtime_profile if items else
                                                                marks[0].provenance.runtime_profile if marks else "lean"),
                            producer_service="cmev-api",
                            derivation_refs=[e.action_id for e in review_events(claim_input)])

    def _map_event(ev: ReviewEvent) -> ReviewEvent:
        if not entry_map:
            return ev
        new_eid = entry_map.get(ev.entry_id, ev.entry_id)
        new_vals = dict(ev.new_values)
        changed = False
        if ev.entry_id != new_eid:
            changed = True
        if "entry_id" in new_vals and new_vals["entry_id"] in entry_map:
            new_vals["entry_id"] = entry_map[new_vals["entry_id"]]
            changed = True
        if "target_entry_id" in new_vals and new_vals["target_entry_id"] in entry_map:
            new_vals["target_entry_id"] = entry_map[new_vals["target_entry_id"]]
            changed = True
        if changed:
            return ev.model_copy(update={"entry_id": new_eid, "new_values": new_vals})
        return ev

    for raw_event in review_events(claim_input):
        event = _map_event(raw_event)
        if event.action_type in MARK_ACTION_TYPES:
            marks = list(apply_review_event(
                marks, event, rows=items, input_revision=revision, provenance=provenance,
                versions={"review": "m9-review/0.1.0"}).marks)
        elif event.action_type == "correct_line_item":
            items = [correct_line_item(item, event.new_values, input_revision=revision, action_id=event.action_id)
                     if item.entry_id == event.entry_id else item for item in items]
        elif event.action_type == "confirm_declaration_completeness" and declarations:
            declarations = [confirm_completeness(declarations[0], event, items=items, revision=revision,
                                                 provenance=provenance)]
    return [with_effective_price(i, marks) for i in items], marks, declarations

