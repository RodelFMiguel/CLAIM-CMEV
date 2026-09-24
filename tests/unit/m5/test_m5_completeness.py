"""The declaration completeness table and the human-only ``explicitly_empty`` path."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from claim_cmev.contracts.common import ContractError, Provenance
from claim_cmev.documents.line_items import CompletenessFacts, confirm_declaration_completeness, decide_completeness

from m5_support import estimate_cells, header_cells, make_page, parse

CLEAN = CompletenessFacts(page_count=1, unreadable_pages=0, partial_pages=0, layout_family="family-a-ruled-grid",
                          layout_family_reason=None, table_found=True, table_terminated=True,
                          continuation_without_header=0, pages_without_table=0, uncertain_required_rows=0,
                          line_item_count=3, unparsed_table_regions=0, subtotal_mismatches=0)
HUMAN = Provenance(source_kind="real", runtime_profile="lean", producer_service="cmev-api")
AT = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)


def test_complete_needs_every_condition():
    assert decide_completeness(CLEAN) == ("complete", ())


@pytest.mark.parametrize("change, reason", [
    ({"partial_pages": 1}, "page_partial"),
    ({"unreadable_pages": 1, "page_count": 2}, "page_unreadable"),
    ({"layout_family": None, "layout_family_reason": "unsupported_layout", "line_item_count": 0,
      "table_found": False}, "unsupported_layout"),
    ({"layout_family": None, "layout_family_reason": "ambiguous_layout_family", "line_item_count": 0,
      "table_found": False}, "ambiguous_layout_family"),
    ({"layout_family": None, "layout_family_reason": "mixed_layout_families"}, "mixed_layout_families"),
    ({"continuation_without_header": 1}, "continuation_without_header"),
    ({"pages_without_table": 1}, "page_without_table"),
    ({"table_terminated": False}, "table_not_terminated"),
    ({"uncertain_required_rows": 1}, "uncertain_required_field"),
    ({"line_item_count": 0}, "no_rows_matched"),
    ({"unparsed_table_regions": 2}, "unparsed_table_text"),
    ({"subtotal_mismatches": 1}, "subtotal_mismatch"),
])
def test_each_partial_condition(change, reason):
    state, reasons = decide_completeness(replace(CLEAN, **change))
    assert state == "partial" and reason in reasons


@pytest.mark.parametrize("change, reason", [
    ({"page_count": 0}, "no_pages"),
    ({"unreadable_pages": 1}, "all_pages_unreadable"),
    ({"unreadable_pages": 3, "page_count": 3}, "all_pages_unreadable"),
])
def test_unreadable_when_no_page_produced_usable_text(change, reason):
    assert decide_completeness(replace(CLEAN, **change)) == ("unreadable", (reason,))


def test_the_parser_can_never_produce_explicitly_empty():
    worst = replace(CLEAN, line_item_count=0, table_found=False, layout_family=None,
                    layout_family_reason="unsupported_layout")
    assert decide_completeness(worst)[0] == "partial"


def _zero_row_declaration():
    result = parse([make_page(header_cells(160) + [("SUB TOTAL", (640, 200, 740, 220), 0.98)])])
    assert result.completeness.state == "partial" and result.line_items == ()
    return result.completeness


def test_surveyor_confirmation_is_the_only_route_to_explicitly_empty():
    parser_record = _zero_row_declaration()
    confirmed = confirm_declaration_completeness(
        parser_record, remaining_entry_ids=[], confirmed_by="surveyor:alice", confirmed_at=AT, review_revision=4,
        input_revision=2, provenance=HUMAN)
    assert (confirmed.state, confirmed.source, confirmed.reasons) == (
        "explicitly_empty", "human_confirmation", ["surveyor_confirmed_empty"])
    assert (confirmed.confirmed_by, confirmed.review_revision, confirmed.input_revision) == ("surveyor:alice", 4, 2)
    assert confirmed.layout_family == parser_record.layout_family
    assert parser_record.state == "partial"  # the parser's record is never edited


def test_confirmation_is_refused_while_rows_remain_or_without_a_new_revision():
    parser_record = _zero_row_declaration()
    kwargs = dict(confirmed_by="surveyor:alice", confirmed_at=AT, review_revision=4, provenance=HUMAN)
    with pytest.raises(ContractError) as rows:
        confirm_declaration_completeness(parser_record, remaining_entry_ids=["li-1"], input_revision=2, **kwargs)
    assert rows.value.reason_code == "declaration_has_rows"
    with pytest.raises(ContractError) as same:
        confirm_declaration_completeness(parser_record, remaining_entry_ids=[], input_revision=1, **kwargs)
    assert same.value.reason_code == "input_revision_not_advanced"
    with pytest.raises(ContractError) as actor:
        confirm_declaration_completeness(parser_record, remaining_entry_ids=[], input_revision=2,
                                         **(kwargs | {"confirmed_by": " "}))
    assert actor.value.reason_code == "confirmation_actor_missing"


def test_complete_parse_has_no_reasons_and_is_parser_sourced():
    from m5_support import item
    result = parse([make_page(estimate_cells([item("BONNET", "REPAIR", "1", "", "350.00")]))])
    decl = result.completeness
    assert (decl.state, decl.reasons, decl.source, decl.confirmed_by) == ("complete", [], "parser", None)
