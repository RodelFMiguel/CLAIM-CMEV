"""Declaration completeness (M5 spec table; data contracts section 7.3).

The parser sets ``complete``, ``partial`` or ``unreadable``. Zero parsed rows is never
an empty estimate. ``explicitly_empty`` comes only from ``confirm_declaration_completeness``,
the pure helper ``cmev-api`` calls when a surveyor confirms that the estimate declares
no repair rows.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from claim_cmev.contracts.common import ContractError, Provenance
from claim_cmev.contracts.documents import CompletenessState, DeclarationCompleteness


@dataclass(frozen=True)
class CompletenessFacts:
    """What the parser observed, reduced to the conditions of the completeness table."""

    page_count: int  # pages expected: parsed pages plus pages M4 could not render
    unreadable_pages: int  # unreadable in M4, no text boxes, or never rendered
    partial_pages: int  # partial in M4
    layout_family: str | None
    layout_family_reason: str | None  # unsupported_layout | ambiguous_layout_family | mixed_layout_families
    table_found: bool
    table_terminated: bool
    continuation_without_header: int
    pages_without_table: int
    uncertain_required_rows: int
    line_item_count: int
    unparsed_table_regions: int
    subtotal_mismatches: int


def decide_completeness(facts: CompletenessFacts) -> tuple[CompletenessState, tuple[str, ...]]:
    """Apply the completeness table. Never returns ``explicitly_empty``."""
    if facts.page_count == 0:
        return "unreadable", ("no_pages",)
    readable = facts.page_count - facts.unreadable_pages
    if readable <= 0:
        return "unreadable", ("all_pages_unreadable",)
    reasons: list[str] = []
    if facts.unreadable_pages:
        reasons.append("page_unreadable")
    if facts.partial_pages:
        reasons.append("page_partial")
    if facts.layout_family is None:
        reasons.append(facts.layout_family_reason or "unsupported_layout")
    if facts.continuation_without_header:
        reasons.append("continuation_without_header")
    if facts.pages_without_table:
        reasons.append("page_without_table")
    if facts.table_found and not facts.table_terminated:
        reasons.append("table_not_terminated")
    if facts.uncertain_required_rows:
        reasons.append("uncertain_required_field")
    if facts.line_item_count == 0:
        reasons.append("no_rows_matched")
    if facts.unparsed_table_regions:
        reasons.append("unparsed_table_text")
    if facts.subtotal_mismatches:
        reasons.append("subtotal_mismatch")
    return ("partial" if reasons else "complete"), tuple(reasons)


def confirm_declaration_completeness(
    current: DeclarationCompleteness, *, remaining_entry_ids: Sequence[str], confirmed_by: str,
    confirmed_at: datetime, review_revision: int, input_revision: int, provenance: Provenance,
    reason_code: str = "surveyor_confirmed_empty",
) -> DeclarationCompleteness:
    """The human-only path to ``explicitly_empty`` (a ``confirm_declaration_completeness`` action).

    Pure: returns a new human-sourced record and never edits the parser's record. The
    action is decision-changing, so the record belongs to the new ``input_revision`` the
    API allocates, which must be later than the parser record's. Refused while any
    declared row remains: those rows must be corrected or removed through their own
    review actions first.
    """
    if not confirmed_by or not confirmed_by.strip():
        raise ContractError("confirmation_actor_missing", "an empty-scope confirmation records the surveyor")
    if remaining_entry_ids:
        raise ContractError("declaration_has_rows",
                            f"{len(remaining_entry_ids)} declared row(s) remain; an estimate with rows is not empty")
    if input_revision <= current.input_revision:
        raise ContractError("input_revision_not_advanced",
                            "a decision-changing confirmation creates a new input revision")
    return DeclarationCompleteness(
        claim_id=current.claim_id, input_revision=input_revision, provenance=provenance, versions=current.versions,
        state="explicitly_empty", reasons=[reason_code], unparsed_region_count=current.unparsed_region_count,
        layout_family=current.layout_family, layout_family_reason=current.layout_family_reason,
        pages_covered=list(current.pages_covered), source="human_confirmation", confirmed_by=confirmed_by,
        confirmed_at=confirmed_at, review_revision=review_revision)
