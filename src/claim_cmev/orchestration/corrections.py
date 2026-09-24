"""Replay validated human corrections over copies of immutable branch records."""
from ..contracts.documents import LineItem, DeclarationCompleteness, with_effective_price
from ..contracts.review import ReviewEvent
from ..documents.pen_marks import MARK_ACTION_TYPES, apply_review_event
from ..contracts.common import Provenance


def review_events(claim_input):
    return [ReviewEvent.model_validate(c["event"]) for c in claim_input.get("corrections", [])
            if c.get("kind") == "review_event"]


def apply_corrections(records, marks, claim_input, revision):
    items = list(records.get("line_item", []))
    declarations = list(records.get("declaration_completeness", []))
    provenance = Provenance(source_kind="real", runtime_profile=(items[0].provenance.runtime_profile if items else
                                                                marks[0].provenance.runtime_profile if marks else "lean"),
                            producer_service="cmev-api",
                            derivation_refs=[e.action_id for e in review_events(claim_input)])
    for event in review_events(claim_input):
        values = event.new_values
        if event.action_type in MARK_ACTION_TYPES:
            marks = list(apply_review_event(
                marks, event, rows=items, input_revision=revision, provenance=provenance,
                versions={"review": "m9-review/0.1.0"}).marks)
        elif event.action_type == "correct_line_item":
            revised = []
            for item in items:
                if item.entry_id != event.entry_id:
                    revised.append(item)
                    continue
                data = item.model_dump()
                data.update(values, input_revision=revision, lineage=item.entry_id,
                            provenance=item.provenance.model_copy(update={"derivation_refs":
                                [*item.provenance.derivation_refs, event.action_id]}))
                if "part_code" in values:
                    data["part_code"] = None if values["part_code"] == "unknown" else values["part_code"]
                    data["part_mapping_status"] = "resolved" if data["part_code"] else "unmapped"
                if "operation" in values:
                    data["operation_mapping_status"] = "unmapped" if values["operation"] == "unknown" else "resolved"
                if "side" in values:
                    data["side_source"] = "absent" if values["side"] == "unknown" else "human_correction"
                data["field_uncertainty"] = [u for u in data["field_uncertainty"] if u["field"] not in values]
                if data["part_code"] is None and not any(u["field"] == "part_code" for u in data["field_uncertainty"]):
                    data["field_uncertainty"].append({"field": "part_code", "reason": "human_unresolved"})
                if data["operation"] == "unknown" and not any(u["field"] == "operation" for u in data["field_uncertainty"]):
                    data["field_uncertainty"].append({"field": "operation", "reason": "human_unresolved"})
                if "printed_line_amount" in values:
                    data.update(effective_price=values["printed_line_amount"], effective_price_source="printed",
                                effective_price_reason=None)
                revised.append(LineItem.model_validate(data))
            items = revised
        elif event.action_type == "confirm_declaration_completeness" and declarations:
            data = declarations[0].model_dump()
            data.update(values, input_revision=revision, confirmed_by=event.actor,
                        confirmed_at=event.recorded_at, review_revision=event.resulting_review_revision,
                        provenance=provenance)
            if values["state"] in ("complete", "explicitly_empty"):
                data["unparsed_region_count"] = 0
            declarations = [DeclarationCompleteness.model_validate(data)]
    return [with_effective_price(i, marks) for i in items], marks, declarations
