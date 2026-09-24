"""Pure review-action rules: classification, validation, idempotent save and replay.

Module 09 "Review lifecycle", "Action classification", "Idempotent save" and "Review
revision model"; application platform sections 3.3, 3.5 and 8; data contracts section 10.

``apply_review_action`` never mutates its input and never reads a clock: the caller
supplies the actor, the time, the idempotency key and the action id, persists the
returned ``RecordedAction`` and, for a decision-changing action, carries out the
``ReassessmentPlan``. Replaying the recorded actions over the initial state with
``replay_review_actions`` reproduces the same state, which is what makes a dismissal
survive a reload.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Literal, NamedTuple

from pydantic import Field, ValidationError

from ..contracts.assessment import Assessment
from ..contracts.common import (
    OPERATIONS,
    PART_CODES,
    RESOLVED_SIDES,
    SIDES,
    ContractError,
    deterministic_id,
)
from ..contracts.review import DECISION_CHANGING_ACTIONS, REVIEW_ACTION_TYPES, ReviewEvent
from .config import ReviewConfig, default_review_config
from .state import (
    BRANCH_STAGES,
    STAGE_ORDER,
    ActionClass,
    MarkView,
    ReassessmentPlan,
    RecordedAction,
    ReviewActionRequest,
    ReviewState,
    _Frozen,
)

# Module 08 "Reassessment and lineage": the stages each decision-changing action reruns.
# Every other branch stage is reused unchanged; no action here reruns a neural model.
RERUN_STAGES: dict[str, tuple[str, ...]] = {
    "confirm_mark": ("consolidate",),
    "reject_mark": ("consolidate",),
    "add_mark": ("consolidate",),
    "correct_mark_link": ("consolidate",),
    "enter_amount": ("consolidate",),
    "correct_line_item": ("consolidate",),
    "confirm_declaration_completeness": ("consolidate",),
    "accept_addition": ("consolidate",),
    "confirm_identity": ("summary", "consolidate"),
    "confirm_coverage": ("summary", "consolidate"),
}
NEURAL_STAGES = frozenset({"parts", "damage", "page_read", "pen_marks"})

_COMMON_FIELDS = frozenset({"action_type", "expected_review_revision", "expected_input_revision"})
ALLOWED_FIELDS: dict[str, frozenset[str]] = {k: _COMMON_FIELDS | v for k, v in {
    "confirm_mark": {"mark_id", "target_entry_id", "amount", "currency", "cost_basis", "note"},
    "reject_mark": {"mark_id", "note"},
    "add_mark": {"entry_id", "page_id", "box_norm", "mark_type", "amount", "currency", "cost_basis", "note"},
    "correct_mark_link": {"mark_id", "target_entry_id", "note"},
    "enter_amount": {"mark_id", "entry_id", "amount", "currency", "cost_basis", "note"},
    "correct_line_item": {"entry_id", "corrections", "reason_code", "note"},
    "confirm_identity": {"part_code", "side", "photo_ids", "entry_id", "note"},
    "confirm_coverage": {"part_code", "side", "photo_ids", "covers_enough", "reason_code", "entry_id", "note"},
    "confirm_declaration_completeness": {"completeness_state", "reason_code", "note"},
    "accept_addition": {"candidate_id", "operation", "quantity", "amount", "currency", "cost_basis",
                        "reason_code", "note"},
    "dismiss_addition": {"candidate_id", "reason_code", "note"},
    "dismiss_finding": {"finding_id", "reason_code", "note"},
    "add_note": {"note", "finding_id", "entry_id", "mark_id", "candidate_id"},
}.items()}
CORRECTABLE_FIELDS = ("part_code", "side", "operation", "quantity", "printed_line_amount")
COMPLETENESS_STATES = ("complete", "partial", "unreadable", "explicitly_empty")
ADDITION_OPERATIONS = tuple(o for o in OPERATIONS if o != "unknown")

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLAIN_DECIMAL = re.compile(r"^\d{1,12}(\.\d{1,6})?$")
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,128}$")


# --- classification ---------------------------------------------------------------------

def classify_review_action(action: str | ReviewActionRequest | ReviewEvent) -> ActionClass:
    """``decision_changing`` or ``review_only`` for an action type (data contracts section 10)."""
    action_type = getattr(action, "action_type", action)
    if action_type not in REVIEW_ACTION_TYPES:
        raise ContractError("action_type_unknown", f"{action_type!r} is not a review action type")
    return "decision_changing" if action_type in DECISION_CHANGING_ACTIONS else "review_only"


def rerun_stages(action: str | ReviewActionRequest | ReviewEvent) -> tuple[str, ...]:
    """The stages an action reruns: empty for review-only actions, M8 at least otherwise."""
    action_type = getattr(action, "action_type", action)
    if classify_review_action(action_type) == "review_only":
        return ()
    return RERUN_STAGES[action_type]


def request_hash(request: ReviewActionRequest) -> str:
    """SHA-256 over the canonical request JSON; the idempotency payload fingerprint."""
    body = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


# --- outcome shapes ---------------------------------------------------------------------

OutcomeKind = Literal["applied", "replayed", "conflict", "refused", "invalid"]


class ActionError(_Frozen):
    reason_code: str
    message: str
    field: str | None = None
    http_status: int = 422


class ConflictInfo(_Frozen):
    """Application platform section 8: the current revision, the actions committed since
    the caller's revision and the caller's submitted values, so local work is kept."""

    current_review_revision: int
    submitted_review_revision: int
    current_input_revision: int
    submitted_input_revision: int | None = None
    actions_since: tuple[str, ...] = ()
    submitted_values: dict[str, Any] = Field(default_factory=dict)


class ActionOutcome(_Frozen):
    """The result of one ``apply_review_action`` call.

    ``state`` is the review state after the call (unchanged unless ``kind`` is
    ``applied``). ``recorded`` is the row set to persist for an applied action.
    ``response()`` is the HTTP body without the state.
    """

    kind: OutcomeKind
    http_status: int
    reason_code: str | None = None
    message: str | None = None
    classification: ActionClass | None = None
    action_id: str | None = None
    review_revision: int
    input_revision: int
    new_input_revision: int | None = None
    event: ReviewEvent | None = None
    plan: ReassessmentPlan | None = None
    recorded: RecordedAction | None = None
    conflict: ConflictInfo | None = None
    errors: tuple[ActionError, ...] = ()
    state: ReviewState = Field(exclude=True)

    @property
    def replayed(self) -> bool:
        return self.kind == "replayed"

    @property
    def ok(self) -> bool:
        return self.kind in ("applied", "replayed")

    def response(self) -> dict[str, Any]:
        body = self.model_dump(mode="json", exclude={"state", "recorded"})
        body["replayed"] = self.replayed
        return body


def _unchanged(state: ReviewState, kind: OutcomeKind, status: int, code: str, message: str,
               **extra: Any) -> ActionOutcome:
    return ActionOutcome(kind=kind, http_status=status, reason_code=code, message=message,
                         review_revision=state.review_revision, input_revision=state.current_input_revision,
                         state=state, **extra)


def _err(code: str, message: str, status: int = 422, field: str | None = None) -> ActionError:
    return ActionError(reason_code=code, message=message, field=field, http_status=status)


# --- value checks -----------------------------------------------------------------------

def _amount(text: str | None, config: ReviewConfig, field: str = "amount") -> ActionError | None:
    """Exact plain decimal: no sign, separator or exponent; places, zero and maximum from config."""
    rules = config.amounts
    if text is None:
        return _err("amount_required", "Enter the amount as a decimal such as 980.00.", 400, field)
    if not _PLAIN_DECIMAL.fullmatch(text):
        return _err("amount_invalid", "Enter a plain decimal such as 980.00, with no sign or separators.", 400, field)
    places = len(text.split(".", 1)[1]) if "." in text else 0
    if places > rules.max_decimal_places:
        return _err("amount_too_precise", f"Use at most {rules.max_decimal_places} decimal places.", 400, field)
    value = Decimal(text)
    if value == 0 and not rules.allow_zero:
        return _err("amount_not_positive", "The amount must be greater than zero.", 400, field)
    if value > rules.max_amount:
        return _err("amount_too_large", f"The amount must not exceed {rules.max_amount}.", 400, field)
    return None


def _quantity(text: str | None, field: str = "quantity") -> ActionError | None:
    if text is None:
        return _err("quantity_required", "Enter a quantity.", 400, field)
    if not _PLAIN_DECIMAL.fullmatch(text) or Decimal(text) <= 0:
        return _err("quantity_invalid", "The quantity must be a positive decimal.", 400, field)
    return None


def _money_context(state: ReviewState, request: ReviewActionRequest) -> tuple[str, str, list[ActionError]]:
    currency = request.currency or state.currency
    basis = request.cost_basis or state.cost_basis
    errors = []
    if currency != state.currency:
        errors.append(_err("currency_mismatch", f"The claim currency is {state.currency}.", 422, "currency"))
    if basis != state.cost_basis:
        errors.append(_err("basis_mismatch", f"The claim cost basis is {state.cost_basis}.", 422, "cost_basis"))
    return currency, basis, errors


def _reason(request: ReviewActionRequest, allowed: Iterable[str], config: ReviewConfig, *,
            required: bool = True, code: str = "reason_invalid") -> list[ActionError]:
    allowed = tuple(allowed)
    if request.reason_code is None:
        return [_err("reason_required", "Choose a reason.", 400, "reason_code")] if required else []
    if request.reason_code not in allowed:
        return [_err(code, f"The reason must be one of {list(allowed)}.", 400, "reason_code")]
    if request.reason_code in config.review.note_required_for_reasons and not (request.note or "").strip():
        return [_err("note_required", "Add a note for the reason 'other'.", 400, "note")]
    return []


def _note_length(request: ReviewActionRequest, limit: int) -> list[ActionError]:
    if request.note is not None and len(request.note) > limit:
        return [_err("note_too_long", f"Notes are limited to {limit} characters.", 400, "note")]
    return []


def _free_reason_code(request: ReviewActionRequest) -> list[ActionError]:
    if request.reason_code is not None and not _REASON_CODE.fullmatch(request.reason_code):
        return [_err("reason_code_invalid", "Reason codes are lower-case identifiers.", 400, "reason_code")]
    return []


def _photos(state: ReviewState, request: ReviewActionRequest) -> list[ActionError]:
    if not request.photo_ids:
        return [_err("photo_ids_required", "Name the photographs the confirmation is based on.", 400, "photo_ids")]
    if len(set(request.photo_ids)) != len(request.photo_ids):
        return [_err("photo_ids_duplicated", "Each photograph may be named once.", 400, "photo_ids")]
    if state.photo_ids is not None:
        missing = [p for p in request.photo_ids if p not in state.photo_ids]
        if missing:
            return [_err("photo_not_found", f"Unknown photographs {missing}.", 404, "photo_ids")]
    return []


def _identity(request: ReviewActionRequest) -> list[ActionError]:
    errors = []
    if request.part_code not in PART_CODES:
        errors.append(_err("value_outside_taxonomy", "The part is not in the part vocabulary.", 422, "part_code"))
    if request.side not in RESOLVED_SIDES:
        errors.append(_err("side_unresolved", "Confirm a resolved side: left, right, centre or not_applicable.",
                           422, "side"))
    return errors


def _entry(state: ReviewState, entry_id: str | None, field: str = "entry_id",
           required: bool = True) -> tuple[Any, list[ActionError]]:
    if entry_id is None:
        return None, ([_err("entry_id_required", "Name the line item.", 400, field)] if required else [])
    item = state.line_item(entry_id)
    if item is None:
        return None, [_err("entry_not_found", f"Line item {entry_id} is not in this assessment.", 404, field)]
    return item, []


def _mark(state: ReviewState, request: ReviewActionRequest) -> tuple[MarkView | None, list[ActionError]]:
    if request.mark_id is None:
        return None, [_err("mark_id_required", "Name the pen mark.", 400, "mark_id")]
    mark = state.effective_marks().get(request.mark_id)
    if mark is None:
        return None, [_err("mark_not_found", f"Pen mark {request.mark_id} is not in this assessment.", 404, "mark_id")]
    return mark, []


def _same_page(mark: MarkView, item: Any) -> list[ActionError]:
    if item is not None and item.page_id != mark.page_id:
        return [_err("entry_not_on_mark_page", "Link a mark only to a row on the same page.", 422, "target_entry_id")]
    return []


def _row_excluded(state: ReviewState, entry_id: str) -> bool:
    finding = state.finding_for_entry(entry_id)
    if finding is not None and finding.row_state == "excluded":
        return True
    return any(m.entry_id == entry_id and m.mark_type == "exclusion" and m.state == "confirmed"
               for m in state.effective_marks().values())


class _Validated(NamedTuple):
    targets: dict[str, str | None]
    original_values: dict[str, Any]
    new_values: dict[str, Any]


Validator = Callable[[ReviewState, ReviewActionRequest, ReviewConfig, str], "list[ActionError] | _Validated"]


# --- per-type validation ----------------------------------------------------------------

def _v_confirm_mark(state, request, config, action_id):
    mark, errors = _mark(state, request)
    if errors:
        return errors
    if mark.state != "pending":
        return [_err("mark_already_resolved", f"Pen mark {mark.mark_id} is already {mark.state}.", 422, "mark_id")]
    target, errors = _entry(state, request.target_entry_id, "target_entry_id", required=False)
    link = request.target_entry_id or mark.entry_id
    if errors:
        return errors
    if link is None:
        return [_err("mark_unlinked", "Link the mark to a row before confirming it.", 422, "target_entry_id")]
    errors = _same_page(mark, target) + _note_length(request, config.review.note_max_chars)
    new: dict[str, Any] = {"state": "confirmed", "entry_id": link, "mark_type": mark.mark_type}
    if link != mark.entry_id:
        new["link_reason"] = "human_link"
    if mark.mark_type == "price_change":
        amount_error = _amount(request.amount, config)
        currency, basis, money_errors = _money_context(state, request)
        errors += ([amount_error] if amount_error else []) + money_errors
        new.update(amount=request.amount, currency=currency, cost_basis=basis)
    elif request.amount is not None:
        errors.append(_err("amount_not_applicable", "An exclusion mark carries no amount.", 400, "amount"))
    if errors:
        return errors
    return _Validated({"mark_id": mark.mark_id, "entry_id": link},
                      {"state": mark.state, "entry_id": mark.entry_id}, new)


def _v_reject_mark(state, request, config, action_id):
    mark, errors = _mark(state, request)
    if errors:
        return errors
    if mark.state != "pending":
        return [_err("mark_already_resolved", f"Pen mark {mark.mark_id} is already {mark.state}.", 422, "mark_id")]
    errors = _note_length(request, config.review.note_max_chars)
    return errors or _Validated({"mark_id": mark.mark_id, "entry_id": mark.entry_id},
                                {"state": mark.state, "entry_id": mark.entry_id}, {"state": "rejected"})


def _v_add_mark(state, request, config, action_id):
    item, errors = _entry(state, request.entry_id)
    if errors:
        return errors
    if request.mark_type not in ("exclusion", "price_change"):
        errors.append(_err("mark_type_invalid", "The mark type is exclusion or price_change.", 400, "mark_type"))
    page_id = request.page_id or item.page_id
    if page_id != item.page_id:
        errors.append(_err("entry_not_on_mark_page", "Add a mark only to a row on the same page.", 422, "page_id"))
    if state.page_ids is not None and page_id not in state.page_ids:
        errors.append(_err("page_not_found", f"Page {page_id} is not in this input.", 404, "page_id"))
    box = request.box_norm or item.row_box_norm
    x0, y0, x1, y1 = box
    if not all(0.0 <= v <= 1.0 for v in box) or x0 > x1 or y0 > y1:
        errors.append(_err("box_invalid", "Boxes are normalised [x_min, y_min, x_max, y_max].", 400, "box_norm"))
    mark_id = deterministic_id("hmark", f"{state.claim_id}:{state.assessment_revision}", action_id)
    new: dict[str, Any] = {"mark_id": mark_id, "entry_id": item.entry_id, "page_id": page_id, "box_norm": list(box),
                           "box_source": "surveyor_box" if request.box_norm else "row_box",
                           "mark_type": request.mark_type, "state": "confirmed", "origin": "human_added",
                           "link_reason": "human_link"}
    if request.mark_type == "price_change":
        amount_error = _amount(request.amount, config)
        currency, basis, money_errors = _money_context(state, request)
        errors += ([amount_error] if amount_error else []) + money_errors
        new.update(amount=request.amount, currency=currency, cost_basis=basis)
    elif request.amount is not None:
        errors.append(_err("amount_not_applicable", "An exclusion mark carries no amount.", 400, "amount"))
    errors += _note_length(request, config.review.note_max_chars)
    return errors or _Validated({"mark_id": mark_id, "entry_id": item.entry_id}, {}, new)


def _v_correct_mark_link(state, request, config, action_id):
    mark, errors = _mark(state, request)
    if errors:
        return errors
    if mark.state == "rejected":
        return [_err("mark_rejected", "A rejected mark cannot be relinked.", 422, "mark_id")]
    if request.target_entry_id is None:
        return [_err("target_entry_required", "Choose the row; reject the mark if it refers to no row.",
                     400, "target_entry_id")]
    target, errors = _entry(state, request.target_entry_id, "target_entry_id")
    if errors:
        return errors
    if target.entry_id == mark.entry_id:
        return [_err("no_change", "The mark is already linked to that row.", 422, "target_entry_id")]
    errors = _same_page(mark, target) + _note_length(request, config.review.note_max_chars)
    original = {"entry_id": mark.entry_id, "candidate_entry_ids": list(mark.candidate_entry_ids)}
    new = {"entry_id": target.entry_id, "link_reason": "human_link", "model_entry_id": mark.entry_id}
    return errors or _Validated({"mark_id": mark.mark_id, "entry_id": target.entry_id}, original, new)


def _v_enter_amount(state, request, config, action_id):
    mark, errors = _mark(state, request)
    if errors:
        return errors
    if mark.mark_type != "price_change":
        return [_err("mark_not_price_change", "Amounts are entered only for a price-change mark.", 422, "mark_id")]
    if mark.state != "confirmed":
        return [_err("mark_not_confirmed", "Confirm the price change with its amount first.", 422, "mark_id")]
    if request.entry_id is not None and request.entry_id != mark.entry_id:
        return [_err("entry_mismatch", "The amount belongs to the row the mark is linked to.", 422, "entry_id")]
    amount_error = _amount(request.amount, config)
    currency, basis, errors = _money_context(state, request)
    errors = ([amount_error] if amount_error else []) + errors + _note_length(request, config.review.note_max_chars)
    if errors:
        return errors
    if mark.confirmed_amount is not None and Decimal(mark.confirmed_amount) == Decimal(request.amount):
        return [_err("no_change", "That amount is already recorded.", 422, "amount")]
    original = {"amount": mark.confirmed_amount, "currency": mark.confirmed_currency,
                "cost_basis": mark.confirmed_cost_basis}
    return _Validated({"mark_id": mark.mark_id, "entry_id": mark.entry_id}, original,
                      {"amount": request.amount, "currency": currency, "cost_basis": basis})


def _v_correct_line_item(state, request, config, action_id):
    item, errors = _entry(state, request.entry_id)
    if errors:
        return errors
    if _row_excluded(state, item.entry_id):
        return [_err("row_excluded", "A confirmed exclusion is not corrected; it is not checked.", 422, "entry_id")]
    fields = request.corrections
    if not fields:
        return [_err("corrections_required", "Change at least one field.", 400, "corrections")]
    unknown = sorted(set(fields) - set(CORRECTABLE_FIELDS))
    if unknown:
        return [_err("correction_field_unknown", f"Fields {unknown} cannot be corrected here.", 400, "corrections")]
    errors = _reason(request, config.review.line_item_correction_reasons, config)
    errors += _note_length(request, config.review.note_max_chars)
    checks = {
        "part_code": lambda v: v in PART_CODES or v == "unknown",
        "side": lambda v: v in SIDES,
        "operation": lambda v: v in OPERATIONS,
    }
    for name, value in fields.items():
        if value is None:
            errors.append(_err("correction_value_required", f"Supply a value for {name}.", 400, name))
        elif name in checks and not checks[name](value):
            errors.append(_err("value_outside_taxonomy", f"{value!r} is not a valid {name}.", 422, name))
        elif name == "quantity" and (e := _quantity(value, name)):
            errors.append(e)
        elif name == "printed_line_amount" and (e := _amount(value, config, name)):
            errors.append(e)
    if errors:
        return errors
    original = {name: getattr(item, name) for name in CORRECTABLE_FIELDS}

    def same(name: str, value: str) -> bool:
        old = original[name]
        if name in ("quantity", "printed_line_amount"):
            return old is not None and Decimal(old) == Decimal(value)
        return old == value or (name == "part_code" and value == "unknown" and old is None)

    if all(same(name, value) for name, value in fields.items()):
        return [_err("no_change", "The corrected values equal the current values.", 422, "corrections")]
    new: dict[str, Any] = dict(fields)
    if "side" in fields:
        new["side_source"] = "human_correction"
    original.update(original_part_text=item.original_part_text, original_operation_text=item.original_operation_text,
                    original_amount_text=item.original_amount_text)
    return _Validated({"entry_id": item.entry_id}, original, new)


def _v_confirm_identity(state, request, config, action_id):
    errors = _identity(request) + _photos(state, request) + _note_length(request, config.review.note_max_chars)
    _, entry_errors = _entry(state, request.entry_id, required=False)
    errors += entry_errors
    if errors:
        return errors
    new = {"part_code": request.part_code, "side": request.side, "photo_ids": list(request.photo_ids),
           "source": "human"}
    return _Validated({"entry_id": request.entry_id}, {"side": "unknown", "source": "model"}, new)


def _v_confirm_coverage(state, request, config, action_id):
    errors = _identity(request) + _photos(state, request) + _free_reason_code(request)
    errors += _note_length(request, config.review.note_max_chars)
    _, entry_errors = _entry(state, request.entry_id, required=False)
    errors += entry_errors
    if request.covers_enough is None:
        errors.append(_err("covers_enough_required", "State whether the views cover enough of the part.",
                           400, "covers_enough"))
    elif not request.covers_enough and request.reason_code is None:
        errors.append(_err("reason_required", "Give a reason when the views do not cover enough.", 400,
                           "reason_code"))
    if errors:
        return errors
    slot = next((c for c in state.coverage if c.part_code == request.part_code and c.side == request.side), None)
    original = {"state": slot.state if slot else None, "reasons": list(slot.reasons) if slot else []}
    new = {"part_code": request.part_code, "side": request.side, "photo_ids": list(request.photo_ids),
           "covers_enough": request.covers_enough, "reason": request.reason_code}
    return _Validated({"entry_id": request.entry_id}, original, new)


def _v_confirm_completeness(state, request, config, action_id):
    errors = _free_reason_code(request) + _note_length(request, config.review.note_max_chars)
    if request.completeness_state not in COMPLETENESS_STATES:
        errors.append(_err("completeness_state_invalid", f"The state is one of {list(COMPLETENESS_STATES)}.",
                           400, "completeness_state"))
    elif request.completeness_state != "complete" and request.reason_code is None:
        errors.append(_err("reason_required", "Give a reason for a state other than complete.", 400, "reason_code"))
    if state.page_ids is not None and not state.page_ids:
        errors.append(_err("no_pages_read", "No estimate page was read for this input.", 422, "completeness_state"))
    if errors:
        return errors
    current = state.completeness
    if current is not None and current.source == "human_confirmation" and current.state == request.completeness_state:
        return [_err("no_change", "That completeness state is already confirmed.", 422, "completeness_state")]
    original = {"state": current.state if current else None, "source": current.source if current else None,
                "reasons": list(current.reasons) if current else []}
    new = {"state": request.completeness_state, "source": "human_confirmation",
           "reasons": [request.reason_code] if request.reason_code else []}
    return _Validated({}, original, new)


def _addition(state: ReviewState, request: ReviewActionRequest) -> tuple[Any, list[ActionError]]:
    if request.candidate_id is None:
        return None, [_err("candidate_id_required", "Name the possible addition.", 400, "candidate_id")]
    candidate = state.candidate(request.candidate_id)
    if candidate is None:
        return None, [_err("addition_not_found", f"Possible addition {request.candidate_id} is not listed.",
                           404, "candidate_id")]
    decided = state.addition_decisions().get(candidate.candidate_id)
    if candidate.review_state != "open" or decided is not None:
        state_text = decided.action_type if decided else candidate.review_state
        return None, [_err("addition_already_decided", f"The addition was already {state_text}.", 422,
                           "candidate_id")]
    return candidate, []


def _v_accept_addition(state, request, config, action_id):
    candidate, errors = _addition(state, request)
    if errors:
        return errors
    if candidate.status != "proposed":
        return [_err("addition_not_proposed", "Only a proposed addition can be added to the scope.", 422,
                     "candidate_id")]
    if request.operation is None:
        errors.append(_err("operation_required", "Choose the repair operation; it is never pre-filled.", 400,
                           "operation"))
    elif request.operation not in ADDITION_OPERATIONS:
        errors.append(_err("value_outside_taxonomy", f"The operation is one of {list(ADDITION_OPERATIONS)}.",
                           422, "operation"))
    if (e := _quantity(request.quantity)):
        errors.append(e)
    errors += _free_reason_code(request) + _note_length(request, config.review.note_max_chars)
    new: dict[str, Any] = {"part_code": candidate.part_code, "side": candidate.side, "operation": request.operation,
                           "quantity": request.quantity, "amount": request.amount}
    if request.amount is None:
        if request.reason_code is None and not (request.note or "").strip():
            errors.append(_err("amount_absent_reason_required", "Say why the amount is left empty.", 400, "amount"))
        new["amount_absent_reason"] = request.reason_code or "surveyor_note"
    else:
        amount_error = _amount(request.amount, config)
        currency, basis, money_errors = _money_context(state, request)
        errors += ([amount_error] if amount_error else []) + money_errors
        new.update(currency=currency, cost_basis=basis)
    if errors:
        return errors
    return _Validated({"candidate_id": candidate.candidate_id},
                      {"review_state": candidate.review_state, "status": candidate.status}, new)


def _v_dismiss_addition(state, request, config, action_id):
    candidate, errors = _addition(state, request)
    if errors:
        return errors
    errors = _reason(request, config.review.dismissal_reasons, config, code="dismissal_reason_invalid")
    errors += _note_length(request, config.review.dismissal_note_max_chars)
    return errors or _Validated({"candidate_id": candidate.candidate_id},
                                {"review_state": candidate.review_state, "status": candidate.status},
                                {"review_state": "dismissed"})


def _v_dismiss_finding(state, request, config, action_id):
    if request.finding_id is None:
        return [_err("finding_id_required", "Name the finding.", 400, "finding_id")]
    finding = state.finding(request.finding_id)
    if finding is None:
        return [_err("finding_not_found", f"Finding {request.finding_id} is not in this assessment.", 404,
                     "finding_id")]
    if finding.row_state == "excluded":
        return [_err("row_excluded", "An excluded row has no result to dismiss.", 422, "finding_id")]
    if finding.finding_id in state.dismissals():
        return [_err("already_dismissed", "This finding is already dismissed.", 422, "finding_id")]
    errors = _reason(request, config.review.dismissal_reasons, config, code="dismissal_reason_invalid")
    errors += _note_length(request, config.review.dismissal_note_max_chars)
    return errors or _Validated({"finding_id": finding.finding_id, "entry_id": finding.entry_id},
                                {"overall_result": finding.overall_result, "content_hash": finding.content_hash},
                                {"dismissed": True})


def _v_add_note(state, request, config, action_id):
    if not (request.note or "").strip():
        return [_err("note_required", "Write the note.", 400, "note")]
    errors = _note_length(request, config.review.note_max_chars)
    if request.finding_id is not None and state.finding(request.finding_id) is None:
        errors.append(_err("finding_not_found", "Unknown finding.", 404, "finding_id"))
    if request.entry_id is not None and state.line_item(request.entry_id) is None:
        errors.append(_err("entry_not_found", "Unknown line item.", 404, "entry_id"))
    if request.mark_id is not None and request.mark_id not in state.effective_marks():
        errors.append(_err("mark_not_found", "Unknown pen mark.", 404, "mark_id"))
    if request.candidate_id is not None and state.candidate(request.candidate_id) is None:
        errors.append(_err("addition_not_found", "Unknown possible addition.", 404, "candidate_id"))
    targets = {"finding_id": request.finding_id, "entry_id": request.entry_id, "mark_id": request.mark_id,
               "candidate_id": request.candidate_id}
    return errors or _Validated(targets, {}, {})


VALIDATORS: dict[str, Validator] = {
    "confirm_mark": _v_confirm_mark, "reject_mark": _v_reject_mark, "add_mark": _v_add_mark,
    "correct_mark_link": _v_correct_mark_link, "enter_amount": _v_enter_amount,
    "correct_line_item": _v_correct_line_item, "confirm_identity": _v_confirm_identity,
    "confirm_coverage": _v_confirm_coverage, "confirm_declaration_completeness": _v_confirm_completeness,
    "accept_addition": _v_accept_addition, "dismiss_addition": _v_dismiss_addition,
    "dismiss_finding": _v_dismiss_finding, "add_note": _v_add_note,
}


# --- fold, plan and replay --------------------------------------------------------------

def _plan(state: ReviewState, event: ReviewEvent) -> ReassessmentPlan:
    """The reassessment the event requires, relative to the assessed input revision."""
    corrections = state.decision_events() + (event,)
    rerun = {stage for e in corrections for stage in RERUN_STAGES[e.action_type]}
    reruns = tuple(s for s in STAGE_ORDER if s in rerun)
    reused = tuple(s for s in BRANCH_STAGES if s not in rerun)
    lineage = tuple(a for s in reused for a in state.base_stage_artifacts.get(s, ()))
    return ReassessmentPlan(
        claim_id=state.claim_id, trigger_action_id=event.action_id, trigger_action_type=event.action_type,
        base_assessment_revision=state.assessment_revision, base_input_revision=state.assessment.input_revision,
        previous_input_revision=state.current_input_revision, new_input_revision=state.current_input_revision + 1,
        review_revision=event.resulting_review_revision, corrections=corrections, rerun_stages=reruns,
        reused_stages=reused, reuse_lineage=lineage,
        publish="consolidate" if reruns == ("consolidate",) else "input_revision_created",
        neural_rerun=bool(rerun & NEURAL_STAGES))


def fold_action(state: ReviewState, event: ReviewEvent, payload_hash: str) -> tuple[ReviewState, RecordedAction]:
    """Append one committed event to the state; the single transition both apply and replay use."""
    if event.claim_id != state.claim_id or event.assessment_revision != state.assessment_revision:
        raise ContractError("replay_mismatch", "the event belongs to another claim or assessment revision")
    if event.expected_review_revision != state.review_revision:
        raise ContractError("replay_out_of_order",
                            f"event expects review revision {event.expected_review_revision}, "
                            f"state is at {state.review_revision}")
    if state.finalization is not None:
        raise ContractError("review_finalized", "a finalized review accepts no further actions")
    plan = _plan(state, event) if event.decision_changing else None
    recorded = RecordedAction(event=event, payload_hash=payload_hash, plan=plan)
    new_state = state.model_copy(update={
        "actions": state.actions + (recorded,),
        "review_revision": event.resulting_review_revision,
        "current_input_revision": plan.new_input_revision if plan else state.current_input_revision,
    })
    return new_state, recorded


def replay_review_actions(initial: ReviewState, actions: Iterable[RecordedAction]) -> ReviewState:
    """Fold recorded actions over an initial state; refuses a plan that does not recompute identically."""
    state = initial
    for recorded in actions:
        state, again = fold_action(state, recorded.event, recorded.payload_hash)
        if recorded.plan is not None and recorded.plan != again.plan:
            raise ContractError("replay_mismatch", f"action {recorded.event.action_id} replays to a different plan")
    return state


def replay_review_events(initial: ReviewState, events: Iterable[ReviewEvent],
                         payload_hashes: Mapping[str, str]) -> ReviewState:
    """Replay stored ``review_event`` rows; ``payload_hashes`` maps idempotency key to request hash."""
    recorded = []
    for event in events:
        if event.idempotency_key not in payload_hashes:
            raise ContractError("replay_mismatch", f"no payload hash for key {event.idempotency_key!r}")
        recorded.append(RecordedAction(event=event, payload_hash=payload_hashes[event.idempotency_key]))
    return replay_review_actions(initial, recorded)


def _applied(state: ReviewState, recorded: RecordedAction, kind: OutcomeKind) -> ActionOutcome:
    event, plan = recorded.event, recorded.plan
    return ActionOutcome(
        kind=kind, http_status=202 if plan else 200, classification=classify_review_action(event),
        action_id=event.action_id, review_revision=event.resulting_review_revision,
        input_revision=plan.new_input_revision if plan else state.current_input_revision,
        new_input_revision=plan.new_input_revision if plan else None, event=event, plan=plan, recorded=recorded,
        state=state)


# --- the entry point --------------------------------------------------------------------

def apply_review_action(
    state: ReviewState,
    request: ReviewActionRequest,
    *,
    actor: str,
    recorded_at: datetime,
    idempotency_key: str,
    action_id: str,
    config: ReviewConfig | None = None,
) -> ActionOutcome:
    """Apply one review action to an explicit review state (pure).

    Order: idempotency (same key and payload replays the original result; same key and
    a different payload is ``idempotency_conflict``), a frozen review is refused, a
    non-current assessment is refused, a stale ``expected_review_revision`` (or
    ``expected_input_revision``) is a conflict carrying the current and submitted
    revisions, then per-type validation. A review-only action yields a new review
    revision and a ``ReviewEvent``; a decision-changing action also yields a
    ``ReassessmentPlan`` for the new input revision.
    """
    config = config or default_review_config()
    if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key or ""):
        return _unchanged(state, "invalid", 400, "idempotency_key_invalid",
                          "The Idempotency-Key is 8 to 128 printable characters.")
    payload_hash = request_hash(request)
    prior = state.recorded(idempotency_key)
    if prior is not None:
        if prior.payload_hash != payload_hash:
            return _unchanged(state, "conflict", 409, "idempotency_conflict",
                              "This request key was already used with different values.")
        return _applied(state, prior, "replayed")
    if state.finalization is not None:
        return _unchanged(state, "refused", 409, "review_finalized",
                          f"Review revision {state.finalization.review_revision} is frozen.")
    if state.claim_assessment_revision != state.assessment_revision:
        return _unchanged(state, "refused", 409, "assessment_not_current",
                          f"Assessment revision {state.claim_assessment_revision} is current; reload it.")
    stale_review = request.expected_review_revision != state.review_revision
    stale_input = (request.expected_input_revision is not None
                   and request.expected_input_revision != state.current_input_revision)
    if stale_review or stale_input:
        since = tuple(r.event.action_id for r in state.actions
                      if r.event.expected_review_revision >= request.expected_review_revision)
        conflict = ConflictInfo(
            current_review_revision=state.review_revision, submitted_review_revision=request.expected_review_revision,
            current_input_revision=state.current_input_revision,
            submitted_input_revision=request.expected_input_revision, actions_since=since,
            submitted_values=request.model_dump(mode="json", exclude_defaults=True))
        code = "stale_revision" if stale_review else "stale_input_revision"
        return _unchanged(state, "conflict", 409, code, "Reload the current review before saving.", conflict=conflict)
    if any(r.event.action_id == action_id for r in state.actions):
        return _unchanged(state, "invalid", 409, "action_id_reused", "The action id is already recorded.")
    extra = sorted(set(request.model_dump(exclude_defaults=True)) - ALLOWED_FIELDS[request.action_type])
    if extra:
        error = _err("field_not_applicable", f"Fields {extra} do not apply to {request.action_type}.", 400)
        return _unchanged(state, "invalid", 400, error.reason_code, error.message, errors=(error,))
    result = VALIDATORS[request.action_type](state, request, config, action_id)
    if isinstance(result, list):
        first = result[0]
        return _unchanged(state, "invalid", first.http_status, first.reason_code, first.message,
                          classification=classify_review_action(request), errors=tuple(result))
    targets = {k: v for k, v in result.targets.items() if v is not None}
    try:
        event = ReviewEvent(
            event_id=deterministic_id("rve", f"{state.claim_id}:{state.assessment_revision}", action_id),
            action_id=action_id, claim_id=state.claim_id, assessment_revision=state.assessment_revision,
            expected_review_revision=state.review_revision, resulting_review_revision=state.review_revision + 1,
            actor=actor, recorded_at=recorded_at, action_type=request.action_type,
            reason_code=request.reason_code, note=request.note, original_values=result.original_values,
            new_values=result.new_values, idempotency_key=idempotency_key, **targets)
    except (ValidationError, ContractError) as exc:
        error = _err("action_invalid", str(exc).splitlines()[0], 400)
        return _unchanged(state, "invalid", 400, error.reason_code, error.message, errors=(error,))
    new_state, recorded = fold_action(state, event, payload_hash)
    return _applied(new_state, recorded, "applied")


class BatchOutcome(_Frozen):
    """An atomic batch: every action applied, or none (application platform section 3.3)."""

    kind: OutcomeKind
    http_status: int
    reason_code: str | None = None
    failed_index: int | None = None
    outcomes: tuple[ActionOutcome, ...]
    review_revision: int
    input_revision: int
    state: ReviewState = Field(exclude=True)


def apply_review_batch(
    state: ReviewState,
    requests: Iterable[ReviewActionRequest],
    *,
    actor: str,
    recorded_at: datetime,
    idempotency_key: str,
    action_ids: Iterable[str],
    config: ReviewConfig | None = None,
) -> BatchOutcome:
    """Apply an ordered batch atomically. Item ``i`` uses key ``f"{idempotency_key}.{i}"``.

    The first item carries the batch's ``expected_review_revision``; later items are
    checked against the revision the previous item produced. A failure returns the
    original state untouched with the failing index.
    """
    requests, action_ids = list(requests), list(action_ids)
    if not requests or len(requests) != len(action_ids):
        raise ContractError("batch_invalid", "a batch has one action id per request and at least one request")
    current, outcomes = state, []
    for index, (request, action_id) in enumerate(zip(requests, action_ids)):
        key = f"{idempotency_key}.{index}"
        replay = current.recorded(key)
        if index and replay is None:
            request = request.model_copy(update={"expected_review_revision": current.review_revision})
        elif index and replay is not None:
            request = request.model_copy(update={"expected_review_revision": replay.event.expected_review_revision})
        outcome = apply_review_action(current, request, actor=actor, recorded_at=recorded_at, idempotency_key=key,
                                      action_id=action_id, config=config)
        outcomes.append(outcome)
        if not outcome.ok:
            return BatchOutcome(kind=outcome.kind, http_status=outcome.http_status, reason_code=outcome.reason_code,
                                failed_index=index, outcomes=tuple(outcomes), review_revision=state.review_revision,
                                input_revision=state.current_input_revision, state=state)
        current = outcome.state
    replayed = all(o.replayed for o in outcomes)
    status = 202 if any(o.plan for o in outcomes) else 200
    return BatchOutcome(kind="replayed" if replayed else "applied", http_status=status, outcomes=tuple(outcomes),
                        review_revision=current.review_revision, input_revision=current.current_input_revision,
                        state=current)


def carryable_dismissals(previous: ReviewState, assessment: Assessment) -> tuple[tuple[str, ReviewEvent], ...]:
    """Dismissals that M8's rule allows to carry into ``assessment``: same finding id and an
    unchanged, non-null ``content_hash``. The API records each carry as its own event."""
    findings = {f.finding_id: f for f in assessment.findings}
    carried = []
    for finding_id, event in previous.dismissals().items():
        old = previous.finding(finding_id)
        new = findings.get(finding_id)
        if old and new and old.content_hash and old.content_hash == new.content_hash:
            carried.append((finding_id, event))
    return tuple(carried)
