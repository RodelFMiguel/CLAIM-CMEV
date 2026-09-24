"""M3 coverage: one ``PartCoverage`` per supported part and side slot, table driven.

A slot is ``(part_code, side)`` when an identity confirmation resolves it, otherwise
``(part_code, unknown)``. A part with resolved sides also keeps an ``unknown`` slot
when unconfirmed photos still show it, so that evidence stays visible. Views of a
resolved slot come only from photos confirmed to show that physical part; views of an
unknown slot come from the remaining photos with an accepted M1 mask for the part.

``COVERAGE_RULES`` is evaluated in order and the first matching row wins. Rows 1-6
follow the M3 specification table; three conservative rows fill cases the table does
not name (``no_view_passes_screen``, ``coverage_confirmed_not_enough``,
``confirmation_names_no_passing_view``). ``adequate`` is reached only with a recorded
coverage confirmation. A failed image branch or photo is a processing failure, never
``not_visible``.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ...contracts.common import PART_CODES, ContractError, deterministic_id
from ...contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    PartCoverage,
    PartPrediction,
    ViewScreen,
)
from .config import SummaryConfig
from .confirmations import ConfirmationIndex, ReuseLineage, SummaryContext, build_confirmation_index, check_inputs, \
    output_provenance
from .screening import screen_view

BranchStatus = Literal["succeeded", "partial", "failed", "skipped_no_photos"]
BRANCH_STATUSES: tuple[str, ...] = ("succeeded", "partial", "failed", "skipped_no_photos")
COVERAGE_STATES = ("adequate", "inadequate", "not_visible", "unresolved")


@dataclass(frozen=True)
class CoverageSlot:
    part_code: str
    side: str
    photo_ids: tuple[str, ...]
    """Photos whose accepted part mask may supply this slot's views."""
    identity_confirmation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotFacts:
    slot: CoverageSlot
    views: tuple[ViewScreen, ...]
    coverage_confirmation: CoverageConfirmation | None
    processing_failed: bool

    @property
    def passing_photo_ids(self) -> tuple[str, ...]:
        return tuple(v.photo_id for v in self.views if v.screen_result == "pass")


@dataclass(frozen=True)
class CoverageDecision:
    rule_id: str
    state: str
    reasons: tuple[str, ...]
    covering_photo_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageRule:
    rule_id: str
    applies: Callable[[SlotFacts], bool]
    state: str
    reasons: Callable[[SlotFacts], tuple[str, ...]]


def _failing_reasons(facts: SlotFacts) -> tuple[str, ...]:
    return tuple(sorted({r for v in facts.views if v.screen_result == "fail" for r in v.reasons}))


def _confirmed_passing(facts: SlotFacts) -> tuple[str, ...]:
    confirmed = set(facts.coverage_confirmation.covering_photo_ids) if facts.coverage_confirmation else set()
    return tuple(p for p in facts.passing_photo_ids if p in confirmed)


COVERAGE_RULES: tuple[CoverageRule, ...] = (
    CoverageRule("processing_failed", lambda f: f.processing_failed, "unresolved", lambda f: ("processing_failed",)),
    CoverageRule("identity_not_resolved", lambda f: f.slot.side == "unknown", "unresolved",
                 lambda f: ("identity_not_resolved",)),
    CoverageRule("no_accepted_part_mask", lambda f: not f.views, "not_visible", lambda f: ("no_accepted_part_mask",)),
    CoverageRule("every_view_fails_screen", lambda f: all(v.screen_result == "fail" for v in f.views), "inadequate",
                 _failing_reasons),
    # Addition: some views could not be screened and none passes; never a pass.
    CoverageRule("no_view_passes_screen", lambda f: not f.passing_photo_ids, "inadequate",
                 lambda f: ("screen_not_run", *_failing_reasons(f))),
    CoverageRule("awaiting_coverage_confirmation", lambda f: f.coverage_confirmation is None, "inadequate",
                 lambda f: ("awaiting_coverage_confirmation",)),
    # Addition: the surveyor recorded that the views do not cover enough of the part.
    CoverageRule("coverage_confirmed_not_enough", lambda f: not f.coverage_confirmation.covers_enough, "inadequate",
                 lambda f: (f.coverage_confirmation.reason or "coverage_confirmed_not_enough",)),
    # Addition: the confirmation names no view of this slot that passes the screen.
    CoverageRule("confirmation_names_no_passing_view", lambda f: not _confirmed_passing(f), "inadequate",
                 lambda f: ("coverage_confirmation_views_mismatch",)),
    CoverageRule("adequate", lambda f: True, "adequate", lambda f: ()),
)


def decide_slot(facts: SlotFacts) -> CoverageDecision:
    """The ordered table: the first matching row decides state and reason codes."""
    for rule in COVERAGE_RULES:
        if rule.applies(facts):
            covering = _confirmed_passing(facts) if rule.state == "adequate" else ()
            return CoverageDecision(rule.rule_id, rule.state, rule.reasons(facts), covering)
    raise AssertionError("the final coverage rule always applies")  # pragma: no cover


# ---------------------------------------------------------------- slots and views
def accepted_views(predictions: Sequence[PartPrediction]) -> dict[tuple[str, str], PartPrediction]:
    """(photo_id, part_code) -> the accepted M1 prediction supplying a view."""
    views: dict[tuple[str, str], PartPrediction] = {}
    for prediction in predictions:
        if not prediction.accepted:
            continue
        key = (prediction.photo_id, prediction.part_code)
        if key in views and views[key].prediction_id != prediction.prediction_id:
            raise ContractError("duplicate_part_prediction", f"two accepted predictions for {key}")
        views[key] = prediction
    return views


def screen_views(views: Mapping[tuple[str, str], PartPrediction],
                 view_signals: Mapping[tuple[str, str], Mapping[str, float]] | None,
                 config: SummaryConfig) -> dict[tuple[str, str], ViewScreen]:
    """Screen each accepted view. Part area comes from the M1 row; pixel signals are supplied."""
    screens = {}
    for key, prediction in views.items():
        frame = prediction.mask_ref.width * prediction.mask_ref.height
        signals = {"part_area_fraction": round(prediction.pixel_count / frame, 6),
                   **dict((view_signals or {}).get(key, {}))}
        screens[key] = screen_view(key[0], signals, config)
    return screens


def build_slots(parts: Iterable[str], index: ConfirmationIndex, views: Mapping[tuple[str, str], PartPrediction],
                observations: Sequence[ImageDamageObservation]) -> list[CoverageSlot]:
    evidence: dict[str, set[str]] = {}
    for photo, part in views:
        evidence.setdefault(part, set()).add(photo)
    for obs in observations:
        if obs.part_code is not None:
            evidence.setdefault(obs.part_code, set()).add(obs.photo_id)
    slots = []
    for part in parts:
        sides = index.resolved_sides(part)
        for side in sides:
            slots.append(CoverageSlot(part, side, index.confirmed_photos(part, side), index.identity_ids(part, side)))
        unconfirmed = sorted(evidence.get(part, set()) - set(index.confirmed_photos(part)))
        if not sides or unconfirmed:
            slots.append(CoverageSlot(part, "unknown", tuple(unconfirmed)))
    return slots


def _slot_parts(supported: Sequence[str], views: Mapping[tuple[str, str], PartPrediction],
                predictions: Sequence[PartPrediction], observations: Sequence[ImageDamageObservation],
                index: ConfirmationIndex) -> list[str]:
    seen = set(supported) | {p.part_code for p in predictions} | {o.part_code for o in observations if o.part_code}
    seen |= {part for _, part in index.identity} | {part for part, _ in index.coverage}
    order = {code: i for i, code in enumerate(PART_CODES)}
    return list(supported) + sorted(seen - set(supported), key=order.__getitem__)


def build_coverage(predictions: Sequence[PartPrediction], observations: Sequence[ImageDamageObservation],
                   index: ConfirmationIndex, *, context: SummaryContext, config: SummaryConfig,
                   lineage: ReuseLineage | None, supported_parts: Sequence[str],
                   view_signals: Mapping[tuple[str, str], Mapping[str, float]] | None = None,
                   branch_status: str = "succeeded", failed_photo_ids: Iterable[str] = ()) -> list[PartCoverage]:
    """Coverage records from validated inputs and an already built confirmation index."""
    if branch_status not in BRANCH_STATUSES:
        raise ContractError("branch_status_unknown", f"{branch_status!r} is not one of {BRANCH_STATUSES}")
    failed_photos = set(failed_photo_ids)
    if branch_status == "partial" and not failed_photos:
        raise ContractError("branch_status_unknown", "a partial image branch names the photos that failed")
    branch_failed = branch_status == "failed"
    views = accepted_views(predictions)
    screens = screen_views(views, view_signals, config)
    provenance = output_provenance(context, lineage)
    records = []
    for slot in build_slots(_slot_parts(supported_parts, views, predictions, observations, index), index, views,
                            observations):
        slot_views = tuple(screens[(photo, slot.part_code)] for photo in slot.photo_ids
                           if (photo, slot.part_code) in screens)
        if slot.side == "unknown":
            failed = branch_failed or bool(failed_photos)
        else:
            failed = branch_failed or bool(set(slot.photo_ids) & failed_photos) or (not slot_views and bool(failed_photos))
        confirmation = None if slot.side == "unknown" else index.coverage_for(slot.part_code, slot.side)
        decision = decide_slot(SlotFacts(slot, slot_views, confirmation, failed))
        reasons = list(decision.reasons)
        if branch_status == "skipped_no_photos":
            reasons.append("no_photographs")
        records.append(PartCoverage(
            claim_id=context.claim_id, input_revision=context.input_revision, provenance=provenance,
            versions=dict(context.versions),
            coverage_id=deterministic_id("pc", context.job_key, slot.part_code, slot.side),
            part_code=slot.part_code, side=slot.side, state=decision.state,
            covering_photo_ids=list(decision.covering_photo_ids), views=list(slot_views), reasons=reasons,
            coverage_confirmation_id=confirmation.confirmation_id if confirmation else None,
            identity_confirmation_ids=list(slot.identity_confirmation_ids)))
    return records


def decide_coverage(
    part_predictions: Sequence[PartPrediction],
    observations: Sequence[ImageDamageObservation],
    identity_confirmations: Sequence[IdentityConfirmation] | ConfirmationIndex,
    coverage_confirmations: Sequence[CoverageConfirmation] = (),
    *,
    context: SummaryContext,
    config: SummaryConfig,
    supported_parts: Sequence[str] | None = None,
    view_signals: Mapping[tuple[str, str], Mapping[str, float]] | None = None,
    branch_status: BranchStatus = "succeeded",
    failed_photo_ids: Iterable[str] = (),
    photo_ids: Iterable[str] | None = None,
) -> list[PartCoverage]:
    """Coverage for every supported part and side slot of one input revision.

    ``view_signals`` maps ``(photo_id, part_code)`` to pixel signals from
    ``compute_view_signals``; part area is always taken from the M1 row. A view with a
    missing signal is ``not_run`` and never counts as a pass.
    """
    lineage = check_inputs(context, config.config_version, observations, part_predictions)
    index = (identity_confirmations if isinstance(identity_confirmations, ConfirmationIndex) else
             build_confirmation_index(identity_confirmations, coverage_confirmations, claim_id=context.claim_id,
                                      input_revision=context.input_revision, photo_ids=photo_ids))
    return build_coverage(part_predictions, observations, index, context=context, config=config, lineage=lineage,
                          supported_parts=supported_parts if supported_parts is not None else config.supported_parts(),
                          view_signals=view_signals, branch_status=branch_status, failed_photo_ids=failed_photo_ids)
