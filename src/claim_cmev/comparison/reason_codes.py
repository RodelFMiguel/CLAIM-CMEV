"""The frozen M8 reason-code catalogue (module 08 "Reason codes").

Every finding, check and addition carries at least one code from this catalogue, and
every sentence the surveyor reads is rendered from a code's fixed display text, never
composed freely. Entries with ``source = "spec"`` copy the module 08 table verbatim;
entries with ``source = "addition"`` were added by this implementation for states the
table does not name (a passed documentary or mark check, an evaluated missing-repairs
check, assessment-level branch reasons). Additions still need the Lane 5 / UI freeze.

The engine creates every reason through ``reason()`` or ``require_code()``, which refuse
an unknown code, so an uncatalogued code can never reach a stored record.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal

from claim_cmev.contracts.common import ContractError, Reason

REASON_CATALOGUE_VERSION = "m8-reasons/0.1.0"

SYNTHETIC_COST_NOTICE = (
    "Reference ranges are synthetic, under the fixed single-part SGD pre-tax no-discount basis. A price "
    "outside the range is an unusual synthetic price, not proof of a wrong price and never proof of fraud."
)
"""SI-07 / SI-10 wording that accompanies every cost-comparison outcome."""

AppliesTo = Literal["result", "row_state", "documentary_check", "mark_check", "photo_check", "cost_check",
                    "any_check", "missing_repairs", "addition", "assessment"]


@dataclass(frozen=True)
class ReasonCodeEntry:
    code: str
    applies_to: AppliesTo
    meaning: str
    display_text: str
    rule_ids: tuple[str, ...]
    source: Literal["spec", "addition"] = "spec"
    notice: str | None = None


def _e(code: str, applies_to: AppliesTo, meaning: str, display: str, rules: str,
       source: Literal["spec", "addition"] = "spec", notice: str | None = None) -> ReasonCodeEntry:
    return ReasonCodeEntry(code, applies_to, meaning, display, tuple(rules.split()), source, notice)


_ENTRIES = (
    # --- module 08 reason-code table, verbatim display text -----------------------------
    _e("branch_missing", "result", "A required branch produced nothing", "Waiting for the other input", "R1"),
    _e("branch_failed", "result", "A required branch failed", "Processing failed, this item was not checked", "R1"),
    _e("mark_pending", "result", "A linked mark is still pending", "Confirm the pen mark on this row", "R2"),
    _e("mark_conflicting", "result", "Two marks on one row contradict each other", "Two pen marks conflict on this row",
       "R2"),
    _e("mark_unlinked", "result", "A mark names this row as a candidate", "A pen mark may belong to this row", "R2"),
    _e("exclusion_confirmed", "row_state", "Confirmed exclusion", "Excluded by the surveyor, not checked", "R3"),
    _e("row_fields_incomplete", "result", "A required field is missing or unreadable", "Some details could not be read",
       "R4"),
    _e("operation_unresolved", "result", "The operation could not be mapped", "The repair operation is unclear", "R4"),
    _e("extraction_incomplete", "result", "The row or page parse was partial", "The estimate was only partly read",
       "R4"),
    _e("page_unreadable", "result", "The page could not be read", "This page could not be read", "R4"),
    _e("part_identity_unresolved", "result", "No single physical part matches", "The part could not be identified",
       "R5"),
    _e("side_unresolved", "result", "Left or right is unknown", "The side of the vehicle is unknown", "R5"),
    _e("coverage_inadequate", "photo_check", "Views do not cover enough of the part", "Take more pictures of this part",
       "R6"),
    _e("coverage_not_visible", "photo_check", "The part appears in no photograph", "This part is not in any photograph",
       "R6"),
    _e("coverage_unresolved", "photo_check", "Coverage could not be decided", "Coverage of this part is unclear",
       "R6 R8"),
    _e("damage_evidence_uncertain", "photo_check", "Observations are below confidence", "The damage evidence is unclear",
       "R7"),
    _e("damage_type_out_of_scope", "photo_check", "The damage type is outside the supported six",
       "This damage type is not supported", "R7"),
    _e("damage_supported", "photo_check", "A supporting observation exists", "Damage visible in the covering views",
       "R8"),
    _e("no_supported_damage_in_adequate_views", "result", "Confidently no supporting damage",
       "No supporting damage detected in adequate views", "R8"),
    _e("amount_unresolved", "cost_check", "A pending price change, so no effective price",
       "The revised amount is not confirmed", "R2 R9"),
    _e("amount_unreadable", "cost_check", "The printed amount could not be read", "The amount could not be read", "R9"),
    _e("quantity_missing", "cost_check", "No quantity on the row", "The quantity is missing", "R10"),
    _e("quantity_not_one", "cost_check", "Quantity is not exactly 1", "Only single-part amounts are compared", "R10"),
    _e("currency_unsupported", "cost_check", "Currency is not the table currency", "Cost comparison supports SGD only",
       "R10"),
    _e("basis_mismatch", "cost_check", "The basis differs from the table basis", "This amount uses a different cost basis",
       "R10"),
    _e("unsupported_combination", "cost_check", "The key is not in the frozen grid",
       "No reference exists for this combination", "R10"),
    _e("unknown_vehicle_class", "cost_check", "The vehicle class could not be resolved", "The vehicle class is unknown",
       "R10"),
    _e("no_key", "cost_check", "The key is not in the published table", "No reference range for this combination",
       "R11"),
    _e("insufficient_support", "cost_check", "Below the independent base-case minimum", "Too few reference cases to compare",
       "R11"),
    _e("range_invalid", "cost_check", "Crossed or malformed bounds", "The reference range is not usable", "R11"),
    _e("zero_width_interval", "cost_check", "The bounds are equal, so no relative deviation",
       "Reference range has a single value", "R12", notice=SYNTHETIC_COST_NOTICE),
    _e("amount_in_range", "cost_check", "Inside the bounds, equality included", "Within the reference range", "R12",
       notice=SYNTHETIC_COST_NOTICE),
    _e("amount_above_range", "cost_check", "Above the upper bound", "Above the reference range", "R12",
       notice=SYNTHETIC_COST_NOTICE),
    _e("amount_below_range", "cost_check", "Below the lower bound", "Below the reference range", "R12",
       notice=SYNTHETIC_COST_NOTICE),
    _e("check_not_reached", "any_check", "The ordered flow stopped earlier", "Not checked", "R1 R2 R3 R4 R5 R6 R7 R8"),
    _e("declaration_incomplete", "missing_repairs", "Completeness not `complete` or confirmed empty",
       "Confirm the estimate is complete", "A1"),
    _e("addition_proposed", "addition", "A candidate addition", "Damage with no matching estimate row", "A8"),
    _e("addition_withheld_identity_unresolved", "addition", "Identity or side unresolved",
       "Damage found, part not identified", "A2"),
    _e("addition_withheld_evidence_uncertain", "addition", "Evidence below threshold or out of scope",
       "Damage evidence unclear", "A3"),
    _e("addition_withheld_coverage", "addition", "Coverage not adequate", "Take more pictures before adding", "A4"),
    _e("addition_withheld_ambiguous_row", "addition", "A row could hide a match",
       "An unclear row may already cover this", "A5"),
    _e("addition_withheld_unlinked_mark", "addition", "An unlinked mark could hide a match", "Resolve the pen mark first",
       "A5"),
    _e("addition_withheld_pending_exclusion", "addition", "A pending exclusion could hide a match",
       "Confirm the pen mark first", "A5"),
    _e("addition_suppressed_confirmed_exclusion_same_part", "addition", "Same physical part already excluded",
       "Damage noted on an excluded row", "A7"),
    # --- implementation additions (pending the Lane 5 / UI freeze) ----------------------
    _e("row_identity_resolved", "documentary_check", "Part and operation mapped; one physical part and side resolved",
       "Row read and matched to one physical part", "R4 R5", "addition"),
    _e("marks_clear", "mark_check", "No pending, conflicting or unlinked pen mark on this row",
       "No open pen mark on this row", "R2 R3", "addition"),
    _e("price_change_confirmed", "mark_check", "A confirmed price change supplies the effective price",
       "Revised amount confirmed by the surveyor", "R2 R9", "addition"),
    _e("no_additions_found", "missing_repairs", "No confident damage lacks a matching row",
       "No damage without a matching estimate row", "A2 A3 A4 A5 A6 A7 A8", "addition"),
    _e("image_branch_failed", "assessment", "The image branch failed", "Photo processing failed", "R1", "addition"),
    _e("image_branch_missing", "assessment", "No photographs were processed", "Waiting for photographs", "R1",
       "addition"),
    _e("document_branch_failed", "assessment", "The document branch failed", "Estimate processing failed", "R1",
       "addition"),
    _e("document_branch_missing", "assessment", "No estimate pages were processed", "Waiting for the estimate", "R1",
       "addition"),
)

REASON_CODES = MappingProxyType({entry.code: entry for entry in _ENTRIES})
"""Read-only code -> entry map; the only vocabulary M8 emits."""
if len(REASON_CODES) != len(_ENTRIES):  # pragma: no cover - guarded by a unit test as well
    raise RuntimeError("duplicate reason code in the M8 catalogue")


def is_known(code: str) -> bool:
    return code in REASON_CODES


def require_code(code: str) -> str:
    """Return ``code`` when catalogued; otherwise refuse it. Nothing uncatalogued is stored."""
    if code not in REASON_CODES:
        raise ContractError("reason_code_unknown", f"{code!r} is not in the M8 reason catalogue")
    return code


def display_text(code: str) -> str:
    return REASON_CODES[require_code(code)].display_text


def reason(code: str) -> Reason:
    """The contract ``Reason`` for ``code`` with its fixed display text."""
    return Reason(code=require_code(code), message=display_text(code))


def catalogue() -> list[dict]:
    """JSON-safe catalogue for the API and UI to render from, in table order."""
    return [{**asdict(entry), "rule_ids": list(entry.rule_ids)} for entry in _ENTRIES]


__all__ = ["REASON_CATALOGUE_VERSION", "REASON_CODES", "SYNTHETIC_COST_NOTICE", "ReasonCodeEntry", "catalogue",
           "display_text", "is_known", "reason", "require_code"]
