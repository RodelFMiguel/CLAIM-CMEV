"""Row-level views over pen marks, for M8 gating and M9 display (pure, no I/O).

A row's mark state is derived from the marks every time, never stored. Only a
``confirmed_exclusion`` removes a row from the checks; ``pending``, ``conflicting`` and
``unlinked_candidate`` withhold the row's decision. Rejected marks are retained but
never count. Precedence, strongest first: conflicting, unlinked_candidate, pending,
confirmed_price_change, confirmed_exclusion, rejected, none.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from claim_cmev.contracts.common import BoxNorm, ContractError, ContractModel, RecordId
from claim_cmev.contracts.documents import PenMark

RowMarkStateCode = Literal[
    "none", "pending", "confirmed_exclusion", "confirmed_price_change", "rejected", "conflicting",
    "unlinked_candidate",
]
WITHHOLDING_STATES = frozenset({"pending", "conflicting", "unlinked_candidate"})
SPEC_MAX_MARKS_PER_ROW = 2
"""Mirrors ``row.max_marks_per_row`` in configs/pipeline/m6_linking.yaml; ``link_marks`` passes the config value."""

_STATE_REASON = {
    "pending": "mark_pending", "unlinked_candidate": "mark_unlinked", "confirmed_exclusion": "exclusion_confirmed",
    "confirmed_price_change": "price_change_confirmed", "rejected": "marks_rejected", "none": None,
}


class RowBox(ContractModel):
    """The M5 row geometry M6 needs; the ``row_boxes`` items of ``cmev.cmd.pen-marks-detect.v1``."""

    entry_id: RecordId
    page_id: RecordId
    row_box_norm: BoxNorm
    amount_box_norm: BoxNorm | None = None


_ROW_FIELDS = ("entry_id", "page_id", "row_box_norm", "amount_box_norm")


def row_box_of(row: Any) -> RowBox:
    """Normalise a ``LineItem``, a ``RowBox`` or a command-payload mapping to a ``RowBox``."""
    if isinstance(row, RowBox):
        return row
    if isinstance(row, Mapping):
        data = {key: row.get(key) for key in _ROW_FIELDS}
    else:
        data = {key: getattr(row, key, None) for key in _ROW_FIELDS}
    try:
        return RowBox.model_validate(data)
    except ValueError as exc:
        raise ContractError("row_invalid", str(exc)) from exc


@dataclass(frozen=True)
class RowMarkState:
    """Derived mark state of one line item."""

    entry_id: str
    state: RowMarkStateCode
    reason: str | None
    linked_mark_ids: tuple[str, ...]
    active_mark_ids: tuple[str, ...]
    unlinked_candidate_mark_ids: tuple[str, ...]
    conflict_reason: str | None = None

    @property
    def withholds_decision(self) -> bool:
        """True when no confident finding may be made for this row."""
        return self.state in WITHHOLDING_STATES

    @property
    def excludes_row(self) -> bool:
        """Only a confirmed exclusion removes the row from the checks; it is a row state, never ``ok``."""
        return self.state == "confirmed_exclusion"


def _entry_id(row: Any) -> str:
    if isinstance(row, str):
        return row
    if isinstance(row, Mapping):
        return row["entry_id"]
    return row.entry_id


def conflict_reason(active: Sequence[PenMark], *, max_marks_per_row: int = SPEC_MAX_MARKS_PER_ROW) -> str | None:
    """Why the non-rejected marks linked to one row conflict, or None (M6 step 9)."""
    price_changes = sum(m.mark_type == "price_change" for m in active)
    exclusions = sum(m.mark_type == "exclusion" for m in active)
    if price_changes and exclusions:
        return "exclusion_and_price_change"
    if price_changes > 1:
        return "multiple_price_changes"
    if len(active) > max_marks_per_row:
        return "too_many_marks"
    return None


def conflicting_entry_ids(marks: Iterable[PenMark], *,
                          max_marks_per_row: int = SPEC_MAX_MARKS_PER_ROW) -> dict[str, str]:
    """Entry IDs whose linked, non-rejected marks conflict, mapped to the conflict reason."""
    by_entry: dict[str, list[PenMark]] = {}
    for mark in marks:
        if mark.entry_id is not None and mark.state != "rejected":
            by_entry.setdefault(mark.entry_id, []).append(mark)
    reasons = {e: conflict_reason(ms, max_marks_per_row=max_marks_per_row) for e, ms in by_entry.items()}
    return {e: r for e, r in reasons.items() if r is not None}


def marks_for_entry(marks: Iterable[PenMark], entry_id: str, *,
                    include_unlinked_candidates: bool = True) -> list[PenMark]:
    """Marks linked to ``entry_id`` (any state), plus unlinked marks naming it as a candidate.

    The result can be passed to ``claim_cmev.contracts.documents.effective_price_for`` with
    ``entry_id=entry_id``.
    """
    return [m for m in marks if m.entry_id == entry_id
            or (include_unlinked_candidates and m.entry_id is None and entry_id in m.candidate_entry_ids)]


def unresolved_marks(marks: Iterable[PenMark], *,
                     max_marks_per_row: int = SPEC_MAX_MARKS_PER_ROW) -> list[PenMark]:
    """Non-rejected marks that are pending, unlinked, or linked to a conflicting row, in input order.

    Finalization is refused while any pending or unlinked mark remains; a conflicting row
    blocks its own checks until the surveyor rejects or relinks one of its marks.
    """
    marks = list(marks)
    conflicting = conflicting_entry_ids(marks, max_marks_per_row=max_marks_per_row)
    return [m for m in marks if m.state != "rejected"
            and (m.state == "pending" or m.entry_id is None or m.entry_id in conflicting)]


def row_mark_state(entry_id: str, marks: Iterable[PenMark], *,
                   max_marks_per_row: int = SPEC_MAX_MARKS_PER_ROW) -> RowMarkState:
    """The derived mark state of one row."""
    marks = list(marks)
    linked = [m for m in marks if m.entry_id == entry_id]
    active = [m for m in linked if m.state != "rejected"]
    candidates = [m for m in marks if m.entry_id is None and m.state != "rejected" and entry_id in m.candidate_entry_ids]
    conflict = conflict_reason(active, max_marks_per_row=max_marks_per_row)
    if conflict:
        state: RowMarkStateCode = "conflicting"
    elif candidates:
        state = "unlinked_candidate"
    elif any(m.state == "pending" for m in active):
        state = "pending"
    elif any(m.mark_type == "price_change" for m in active):
        state = "confirmed_price_change"
    elif active:
        state = "confirmed_exclusion"
    elif linked:
        state = "rejected"
    else:
        state = "none"
    return RowMarkState(
        entry_id=entry_id, state=state, reason=conflict if conflict else _STATE_REASON[state],
        linked_mark_ids=tuple(m.mark_id for m in linked), active_mark_ids=tuple(m.mark_id for m in active),
        unlinked_candidate_mark_ids=tuple(m.mark_id for m in candidates), conflict_reason=conflict)


def row_mark_states(rows: Iterable[Any], marks: Iterable[PenMark], *,
                    max_marks_per_row: int = SPEC_MAX_MARKS_PER_ROW) -> dict[str, RowMarkState]:
    """Each row's derived mark state, keyed by entry ID in the given row order.

    ``rows`` may be entry IDs, ``LineItem`` records, ``RowBox`` records or mappings with
    an ``entry_id``.
    """
    marks = list(marks)
    return {e: row_mark_state(e, marks, max_marks_per_row=max_marks_per_row) for e in map(_entry_id, rows)}
