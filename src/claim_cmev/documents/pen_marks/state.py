"""M6 pen-mark state machine (pure, no I/O; imported by ``cmev-api``).

States are ``pending``, ``confirmed`` and ``rejected``; detection produces ``pending``
only. ``apply_mark_action`` applies one surveyor action and returns new records; the
inputs are never mutated. ``cmev-api`` owns endpoints, idempotency keys, review
revisions and the new input/assessment revision each of these decision-changing actions
creates; this module owns the rules:

- ``confirm_mark``: pending to confirmed. The mark must be linked (an optional
  ``target_entry_id`` relinks it in the same action). A price change needs an exact,
  positive, human-typed ``confirmed_amount`` with currency and cost basis; an exclusion
  takes no amount. A TrOCR suggestion is never read.
- ``reject_mark``: pending to rejected; the record is retained. Reason ``not_a_row``
  (the UI's "Not a row") needs a note.
- ``correct_mark_link``: sets ``entry_id`` to a row on the mark's page with
  ``link_reason = human_link``, keeping the model's link in ``model_entry_id``. Allowed on
  pending and confirmed marks; the state is unchanged. A pending mark stays without
  decision fields (the review event carries the actor).
- ``add_mark``: a missed mark, ``origin = human_added``, ``state = confirmed``, no
  detection confidence, linked to the given row.
- ``enter_amount``: sets or replaces the amount of a confirmed price change.

Confirming one mark never changes another. An action on an unknown mark or an illegal
transition raises ``ContractError`` with a stable reason code (``ACTION_ERROR_CODES``).
Repeating an action with the same ``action_id`` returns the records unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import ValidationError

from claim_cmev.contracts.common import ContractError, Provenance, deterministic_id, to_decimal
from claim_cmev.contracts.documents import PenMark
from claim_cmev.contracts.review import ReviewEvent

from .rows import RowBox, row_box_of

MarkActionType = Literal["confirm_mark", "reject_mark", "add_mark", "correct_mark_link", "enter_amount"]
MARK_ACTION_TYPES: tuple[str, ...] = get_args(MarkActionType)
HUMAN_LINK_REASON = "human_link"
HUMAN_RULE_ID = "human"
NOT_A_ROW_REASON = "not_a_row"
MAX_AMOUNT_PLACES = 2
ACTION_ERROR_CODES = frozenset({
    "action_unsupported", "field_required", "field_not_allowed", "mark_not_found", "duplicate_mark_id",
    "mark_already_decided", "mark_not_confirmed", "mark_rejected", "mark_type_mismatch", "mark_unlinked",
    "amount_required", "amount_invalid", "amount_not_allowed", "currency_mismatch", "cost_basis_mismatch",
    "rows_required", "entry_not_found", "entry_on_other_page", "note_required", "mark_id_conflict",
    "context_required", "claim_mismatch", "mark_record_invalid", "row_invalid",
})

_AMOUNT_FIELDS = ("confirmed_amount", "confirmed_currency", "confirmed_cost_basis")
_ALLOWED = {
    "confirm_mark": {"mark_id", "target_entry_id", *_AMOUNT_FIELDS, "reason_code", "note"},
    "reject_mark": {"mark_id", "reason_code", "note"},
    "correct_mark_link": {"mark_id", "target_entry_id", "reason_code", "note"},
    "enter_amount": {"mark_id", *_AMOUNT_FIELDS, "reason_code", "note"},
    "add_mark": {"mark_id", "target_entry_id", "mark_type", "page_id", "box_norm", *_AMOUNT_FIELDS, "reason_code",
                 "note"},
}
_IDENTITY_FIELDS = {"schema_version", "claim_id", "input_revision", "provenance", "versions"}


@dataclass(frozen=True)
class MarkAction:
    """One surveyor action on a pen mark (the mark subset of ``ReviewEvent`` action types).

    ``action_id`` is the review action's ID and becomes ``decision_action_id``. Amounts
    are exact decimal strings typed by the surveyor, never a TrOCR suggestion.
    """

    action_type: str
    action_id: str
    mark_id: str | None = None
    target_entry_id: str | None = None
    confirmed_amount: str | None = None
    confirmed_currency: str | None = None
    confirmed_cost_basis: str | None = None
    reason_code: str | None = None
    note: str | None = None
    mark_type: str | None = None
    page_id: str | None = None
    box_norm: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class MarkTransition:
    """The full mark list after the action, plus what changed, for the review event."""

    marks: tuple[PenMark, ...]
    mark_id: str
    before: PenMark | None
    after: PenMark
    changed: bool
    replay: bool
    original_values: dict[str, Any]
    new_values: dict[str, Any]
    decision_changing: bool = True


@dataclass(frozen=True)
class _Row:
    box: RowBox
    currency: str | None
    cost_basis: str | None
    claim_id: str | None
    input_revision: int | None


def _rows_index(rows: Sequence[Any] | None) -> dict[str, _Row] | None:
    if rows is None:
        return None
    index: dict[str, _Row] = {}
    for row in rows:
        box = row_box_of(row)
        get = row.get if isinstance(row, Mapping) else (lambda key, _row=row: getattr(_row, key, None))
        index[box.entry_id] = _Row(box, get("currency"), get("cost_basis"), get("claim_id"), get("input_revision"))
    return index


def _target_row(entry_id: str, page_id: str, rows: dict[str, _Row] | None) -> _Row:
    if rows is None:
        raise ContractError("rows_required", "linking a mark needs the claim's line-item rows")
    if entry_id not in rows:
        raise ContractError("entry_not_found", entry_id)
    if rows[entry_id].box.page_id != page_id:
        raise ContractError("entry_on_other_page", f"{entry_id} is not on page {page_id}")
    return rows[entry_id]


def _amount_updates(action: MarkAction, row: _Row | None) -> dict[str, str]:
    amount, currency, basis = (getattr(action, f) for f in _AMOUNT_FIELDS)
    if amount is None or currency is None or basis is None:
        raise ContractError("amount_required",
                            "a confirmed price change needs confirmed_amount, confirmed_currency and "
                            "confirmed_cost_basis typed by the surveyor")
    try:
        value = to_decimal(amount) if isinstance(amount, str) else None
    except ValueError:
        value = None
    if value is None or value <= 0 or -value.as_tuple().exponent > MAX_AMOUNT_PLACES:
        raise ContractError("amount_invalid",
                            f"{amount!r} is not a positive decimal string with at most {MAX_AMOUNT_PLACES} places")
    _check_row_money(currency, basis, row)
    return {"confirmed_amount": amount, "confirmed_currency": currency, "confirmed_cost_basis": basis}


def _check_row_money(currency: str | None, basis: str | None, row: _Row | None) -> None:
    """An entered amount must use the row's currency and cost basis when the row states them."""
    if row is not None and row.currency is not None and currency != row.currency:
        raise ContractError("currency_mismatch", f"{currency} differs from the row currency {row.currency}")
    if row is not None and row.cost_basis is not None and basis != row.cost_basis:
        raise ContractError("cost_basis_mismatch", f"{basis} differs from the row cost basis {row.cost_basis}")


def _no_amount(action: MarkAction) -> None:
    if any(getattr(action, f) is not None for f in _AMOUNT_FIELDS):
        raise ContractError("amount_not_allowed", "an exclusion mark carries no amount")


def _rebuild(mark: PenMark, **updates: Any) -> PenMark:
    try:
        return PenMark.model_validate({**mark.model_dump(), **updates})
    except ValidationError as exc:
        raise ContractError("mark_record_invalid", str(exc)) from exc


def _relink(mark: PenMark, target: str) -> dict[str, Any]:
    model_entry = mark.model_entry_id
    if model_entry is None and mark.origin == "detector" and mark.link_reason != HUMAN_LINK_REASON:
        model_entry = mark.entry_id
    return {"entry_id": target, "link_reason": HUMAN_LINK_REASON, "model_entry_id": model_entry}


def _diff(before: PenMark | None, after: PenMark) -> tuple[dict[str, Any], dict[str, Any]]:
    new = {k: v for k, v in after.model_dump(mode="json").items() if k not in _IDENTITY_FIELDS}
    if before is None:
        return {}, new
    old = before.model_dump(mode="json")
    changed = [k for k, v in new.items() if old.get(k) != v]
    return {k: old.get(k) for k in changed}, {k: new[k] for k in changed}


def _check_fields(action: MarkAction) -> None:
    if action.action_type not in MARK_ACTION_TYPES:
        raise ContractError("action_unsupported", f"{action.action_type!r} is not a pen-mark action")
    if not action.action_id:
        raise ContractError("field_required", "action_id")
    extra = sorted(f.name for f in fields(action) if f.name not in {"action_type", "action_id"}
                   and getattr(action, f.name) is not None and f.name not in _ALLOWED[action.action_type])
    if extra:
        raise ContractError("field_not_allowed", f"{action.action_type} does not take {extra}")
    if action.action_type != "add_mark" and not action.mark_id:
        raise ContractError("field_required", "mark_id")


def transition_mark(
    marks: Sequence[PenMark],
    action: MarkAction,
    *,
    actor: str,
    recorded_at: datetime,
    review_revision: int,
    rows: Sequence[Any] | None = None,
    input_revision: int | None = None,
    claim_id: str | None = None,
    provenance: Provenance | None = None,
    versions: Mapping[str, str] | None = None,
) -> MarkTransition:
    """Apply one action and describe the transition. See ``apply_mark_action``."""
    _check_fields(action)
    marks = list(marks)
    ids = [m.mark_id for m in marks]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate_mark_id", "the mark list repeats a mark_id")
    row_index = _rows_index(rows)
    claims = {m.claim_id for m in marks} | ({claim_id} if claim_id else set())
    claims |= {r.claim_id for r in (row_index or {}).values() if r.claim_id}
    if len(claims) > 1:
        raise ContractError("claim_mismatch", f"marks, rows and action span claims {sorted(claims)}")
    if not actor:
        raise ContractError("field_required", "actor")
    decision = {"decision_action_id": action.action_id, "decided_by": actor, "decided_at": recorded_at,
                "review_revision": review_revision}
    position = {m.mark_id: i for i, m in enumerate(marks)}

    if action.action_type == "add_mark":
        before, after, replay = None, *_add_mark(marks, position, action, decision, row_index, input_revision,
                                                 claims, provenance, versions)
    else:
        if action.mark_id not in position:
            raise ContractError("mark_not_found", action.mark_id or "")
        before = marks[position[action.mark_id]]
        replay = before.decision_action_id == action.action_id
        after = before if replay else _HANDLERS[action.action_type](before, action, decision, row_index)
    if before is None and not replay:
        marks.append(after)
    elif after is not before:
        marks[position[after.mark_id]] = after
    original_values, new_values = ({}, {}) if replay else _diff(before, after)
    if input_revision is not None:
        marks = [m if m.input_revision == input_revision else _rebuild(m, input_revision=input_revision)
                 for m in marks]
        after = marks[[m.mark_id for m in marks].index(after.mark_id)]
    return MarkTransition(tuple(marks), after.mark_id, before, after, changed=bool(new_values), replay=replay,
                          original_values=original_values, new_values=new_values)


def _confirm(mark: PenMark, action: MarkAction, decision: dict, rows: dict[str, _Row] | None) -> PenMark:
    if mark.state != "pending":
        raise ContractError("mark_already_decided", f"{mark.mark_id} is already {mark.state}")
    updates: dict[str, Any] = {}
    if action.target_entry_id is not None and action.target_entry_id != mark.entry_id:
        _target_row(action.target_entry_id, mark.page_id, rows)
        updates.update(_relink(mark, action.target_entry_id))
    entry_id = updates.get("entry_id", mark.entry_id)
    if entry_id is None:
        raise ContractError("mark_unlinked", "link the mark to a row before confirming it")
    row = _target_row(entry_id, mark.page_id, rows) if rows is not None else None
    if mark.mark_type == "exclusion":
        _no_amount(action)
    else:
        updates.update(_amount_updates(action, row))
    return _rebuild(mark, state="confirmed", **updates, **decision)


def _reject(mark: PenMark, action: MarkAction, decision: dict, rows: dict[str, _Row] | None) -> PenMark:
    if mark.state != "pending":
        raise ContractError("mark_already_decided", f"{mark.mark_id} is already {mark.state}")
    if action.reason_code == NOT_A_ROW_REASON and not (action.note and action.note.strip()):
        raise ContractError("note_required", "'not a row' needs a short note")
    return _rebuild(mark, state="rejected", **decision)


def _correct_link(mark: PenMark, action: MarkAction, decision: dict, rows: dict[str, _Row] | None) -> PenMark:
    if mark.state == "rejected":
        raise ContractError("mark_rejected", f"{mark.mark_id} was rejected and cannot be relinked")
    if action.target_entry_id is None:
        raise ContractError("field_required", "target_entry_id")
    row = _target_row(action.target_entry_id, mark.page_id, rows)
    if mark.entry_id == action.target_entry_id and mark.link_reason == HUMAN_LINK_REASON:
        return mark
    updates = _relink(mark, action.target_entry_id)
    if mark.state == "confirmed":
        if mark.mark_type == "price_change":
            _check_row_money(mark.confirmed_currency, mark.confirmed_cost_basis, row)
        updates.update(decision)
    return _rebuild(mark, **updates)


def _enter_amount(mark: PenMark, action: MarkAction, decision: dict, rows: dict[str, _Row] | None) -> PenMark:
    if mark.mark_type != "price_change":
        raise ContractError("mark_type_mismatch", "only a price-change mark carries an amount")
    if mark.state != "confirmed":
        raise ContractError("mark_not_confirmed",
                            f"{mark.mark_id} is {mark.state}; confirm the price change with its amount instead")
    row = _target_row(mark.entry_id, mark.page_id, rows) if rows is not None and mark.entry_id else None
    return _rebuild(mark, **_amount_updates(action, row), **decision)


_HANDLERS = {"confirm_mark": _confirm, "reject_mark": _reject, "correct_mark_link": _correct_link,
             "enter_amount": _enter_amount}


def _add_mark(marks: list[PenMark], position: dict[str, int], action: MarkAction, decision: dict,
              rows: dict[str, _Row] | None, input_revision: int | None, claims: set[str],
              provenance: Provenance | None, versions: Mapping[str, str] | None) -> tuple[PenMark, bool]:
    for name in ("mark_type", "page_id", "box_norm", "target_entry_id"):
        if getattr(action, name) is None:
            raise ContractError("field_required", f"add_mark needs {name}")
    mark_id = action.mark_id or deterministic_id("pm", "human_added", action.action_id)
    if mark_id in position:
        existing = marks[position[mark_id]]
        if existing.decision_action_id == action.action_id:
            return existing, True
        raise ContractError("mark_id_conflict", f"{mark_id} already exists")
    row = _target_row(action.target_entry_id, action.page_id, rows)
    revision = input_revision or (max(m.input_revision for m in marks) if marks else row.input_revision)
    claim = next(iter(claims), None) or row.claim_id
    missing = [n for n, v in (("claim_id", claim), ("input_revision", revision), ("provenance", provenance),
                              ("versions", versions)) if not v]
    if missing:
        raise ContractError("context_required", f"add_mark needs {missing}")
    if action.mark_type == "exclusion":
        _no_amount(action)
        amount: dict[str, str] = {}
    elif action.mark_type == "price_change":
        amount = _amount_updates(action, row)
    else:
        raise ContractError("field_not_allowed", f"mark_type {action.mark_type!r} is not exclusion or price_change")
    try:
        mark = PenMark(
            claim_id=claim, input_revision=revision, provenance=provenance, versions=dict(versions),
            mark_id=mark_id, page_id=action.page_id, box_norm=action.box_norm, mark_type=action.mark_type,
            detection_confidence=None, entry_id=action.target_entry_id, candidate_entry_ids=[action.target_entry_id],
            link_reason=HUMAN_LINK_REASON, state="confirmed", origin="human_added", model_entry_id=None,
            rule_id=HUMAN_RULE_ID, **amount, **decision)
    except ValidationError as exc:
        raise ContractError("mark_record_invalid", str(exc)) from exc
    return mark, False


def apply_mark_action(
    marks: Sequence[PenMark],
    action: MarkAction,
    *,
    actor: str,
    recorded_at: datetime,
    review_revision: int,
    rows: Sequence[Any] | None = None,
    input_revision: int | None = None,
    claim_id: str | None = None,
    provenance: Provenance | None = None,
    versions: Mapping[str, str] | None = None,
) -> list[PenMark]:
    """Apply one surveyor mark action to the claim's marks and return the new mark list.

    ``marks`` is every mark of the claim revision (so a conflict or a duplicate ID is
    visible). ``actor``, ``recorded_at`` and ``review_revision`` (the resulting review
    revision that carries the action) become the decision fields. ``rows`` (``LineItem``,
    ``RowBox`` or ``row_boxes`` mappings) is required to link a mark (``correct_mark_link``,
    ``add_mark``, a relinking ``confirm_mark``) and, when given as ``LineItem`` records, also
    checks the currency and cost basis of an entered amount. ``input_revision``, when given,
    stamps every returned mark with the new input revision the action creates.
    ``claim_id``, ``provenance`` and ``versions`` describe a human-added mark (the
    provenance of the API that recorded it); they are required for ``add_mark`` when they
    cannot be taken from the existing marks or rows.

    Returns new records in the input order, with an added mark appended. Raises
    ``ContractError`` with a code from ``ACTION_ERROR_CODES``.
    """
    return list(transition_mark(marks, action, actor=actor, recorded_at=recorded_at, review_revision=review_revision,
                                rows=rows, input_revision=input_revision, claim_id=claim_id, provenance=provenance,
                                versions=versions).marks)


def mark_action_from_review_event(event: ReviewEvent) -> MarkAction:
    """Build a ``MarkAction`` from a saved ``ReviewEvent`` of a pen-mark action type.

    ``event.entry_id`` is the relink target for ``correct_mark_link``, ``add_mark`` and
    ``confirm_mark`` (confirm relinks only when it differs from the mark's row). Amounts,
    ``mark_type``, ``page_id``, ``box_norm`` and an optional explicit ``mark_id`` come
    from ``event.new_values``.
    """
    if event.action_type not in MARK_ACTION_TYPES:
        raise ContractError("action_unsupported", f"{event.action_type!r} is not a pen-mark action")
    values = event.new_values
    box = values.get("box_norm")
    return MarkAction(
        action_type=event.action_type, action_id=event.action_id,
        mark_id=event.mark_id or values.get("mark_id"),
        target_entry_id=event.entry_id if event.action_type in {"correct_mark_link", "add_mark", "confirm_mark"}
        else None,
        confirmed_amount=values.get("confirmed_amount", values.get("amount")), confirmed_currency=values.get("confirmed_currency", values.get("currency")),
        confirmed_cost_basis=values.get("confirmed_cost_basis", values.get("cost_basis")), reason_code=event.reason_code, note=event.note,
        mark_type=values.get("mark_type") if event.action_type == "add_mark" else None, page_id=values.get("page_id"),
        box_norm=tuple(box) if box is not None else None)


def apply_review_event(marks: Sequence[PenMark], event: ReviewEvent, *, rows: Sequence[Any] | None = None,
                       input_revision: int | None = None, provenance: Provenance | None = None,
                       versions: Mapping[str, str] | None = None) -> MarkTransition:
    """``transition_mark`` driven by a ``ReviewEvent``: actor, time and resulting review revision come from it."""
    return transition_mark(marks, mark_action_from_review_event(event), actor=event.actor,
                           recorded_at=event.recorded_at, review_revision=event.resulting_review_revision,
                           rows=rows, input_revision=input_revision, claim_id=event.claim_id,
                           provenance=provenance, versions=versions)


__all__ = [
    "ACTION_ERROR_CODES", "MARK_ACTION_TYPES", "MarkAction", "MarkActionType", "MarkTransition",
    "apply_mark_action", "apply_review_event", "mark_action_from_review_event", "transition_mark",
]
