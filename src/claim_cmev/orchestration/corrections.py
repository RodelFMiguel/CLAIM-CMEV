"""Replay validated human corrections over copies of immutable branch records."""
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


def apply_corrections(records, marks, claim_input, revision):
    items = list(records.get("line_item", []))
    declarations = list(records.get("declaration_completeness", []))
    provenance = Provenance(source_kind="real", runtime_profile=(items[0].provenance.runtime_profile if items else
                                                                marks[0].provenance.runtime_profile if marks else "lean"),
                            producer_service="cmev-api",
                            derivation_refs=[e.action_id for e in review_events(claim_input)])
    for event in review_events(claim_input):
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
