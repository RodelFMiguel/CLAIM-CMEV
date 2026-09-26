"""Conservative M3 grouping of damage observations under a part identity.

Every observation lands in exactly one group, first matching row wins:

* ``resolved``: a recorded identity confirmation covers the observation's photo and
  part; the group key is ``(part_code, side)`` and the side comes only from that
  confirmation. Only this status can support a negative finding in M8.
* ``part_only``: an assigned part code without a confirmation; side stays ``unknown``.
* ``unresolved``: no part code; one group per observation, kept visible.

A group says the observations refer to one part, not that they show one physical
dent. No instance count is inferred and member areas are never summed: the
representative area is the largest single member (``max_member``).
"""
from __future__ import annotations

from collections.abc import Sequence

from ...contracts.common import PART_CODES, SIDES, ContractError, deterministic_id
from ...contracts.imaging import IdentityConfirmation, ImageDamageObservation, PartSummary
from .config import SummaryConfig
from .confirmations import ConfirmationIndex, ReuseLineage, SummaryContext, build_confirmation_index, check_inputs, \
    output_provenance

_STATUS_ORDER = {"resolved": 0, "part_only": 1, "unresolved": 2}
_PART_ORDER = {code: i for i, code in enumerate(PART_CODES)}
_SIDE_ORDER = {side: i for i, side in enumerate(SIDES)}


def unique_observations(observations: Sequence[ImageDamageObservation]) -> list[ImageDamageObservation]:
    """Drop identical redelivered rows; reject two different rows with one ID."""
    by_id: dict[str, ImageDamageObservation] = {}
    for obs in observations:
        previous = by_id.get(obs.observation_id)
        if previous is not None and previous != obs:
            raise ContractError("observation_id_conflict", f"{obs.observation_id} was supplied twice, differently")
        by_id[obs.observation_id] = obs
    return sorted(by_id.values(), key=lambda o: (o.photo_id, o.observation_id))


def group_key(obs: ImageDamageObservation, index: ConfirmationIndex) -> tuple[str, str, str]:
    """(identity_status, part code or observation ID, side) for one observation."""
    if obs.part_code is None:
        return ("unresolved", obs.observation_id, "unknown")
    side = index.identity_side(obs.photo_id, obs.part_code)
    if side is not None:
        return ("resolved", obs.part_code, side)
    return ("part_only", obs.part_code, "unknown")


def _order(key: tuple[str, str, str]) -> tuple:
    status, part, side = key
    return (_STATUS_ORDER[status], _PART_ORDER.get(part, len(_PART_ORDER)), part, _SIDE_ORDER[side])


def build_groups(observations: Sequence[ImageDamageObservation], index: ConfirmationIndex, *,
                 context: SummaryContext, lineage: ReuseLineage | None) -> list[PartSummary]:
    """Group validated observations with an already built confirmation index."""
    members: dict[tuple[str, str, str], list[ImageDamageObservation]] = {}
    for obs in unique_observations(observations):
        members.setdefault(group_key(obs, index), []).append(obs)
    provenance = output_provenance(context, lineage)
    summaries = []
    for key in sorted(members, key=_order):
        status, part, side = key
        group = members[key]
        largest = max(group, key=lambda o: o.area_fraction)  # first of equals, in member order
        identity_ids = sorted({index.identity[(o.photo_id, o.part_code)].confirmation_id for o in group
                               if status == "resolved"})
        if status == "resolved":
            reasons: list[str] = []
        elif status == "part_only":
            reasons = ["identity_not_resolved"]
        else:
            reasons = [largest.part_reason]
        summaries.append(PartSummary(
            claim_id=context.claim_id, input_revision=context.input_revision, provenance=provenance,
            versions=dict(context.versions), summary_id=deterministic_id("ps", context.job_key, status, part, side),
            identity_status=status, part_code=None if status == "unresolved" else part, side=side,
            member_observation_ids=[o.observation_id for o in group], observation_count=len(group),
            damage_codes=sorted({o.damage_code for o in group}),
            supporting_photo_ids=sorted({o.photo_id for o in group}), mask_refs=[o.damage_mask_ref for o in group],
            aggregation_method="max_member", representative_area_fraction=largest.area_fraction,
            representative_observation_id=largest.observation_id,
            max_confidence=max(o.damage_confidence for o in group), identity_confirmation_ids=identity_ids,
            reasons=reasons))
    return summaries


def group_observations(observations: Sequence[ImageDamageObservation],
                       identity_confirmations: Sequence[IdentityConfirmation] | ConfirmationIndex, *,
                       context: SummaryContext, config: SummaryConfig) -> list[PartSummary]:
    """Group every observation of one input revision; no observation is ever dropped."""
    lineage = check_inputs(context, config.config_version, observations, ())
    index = (identity_confirmations if isinstance(identity_confirmations, ConfirmationIndex) else
             build_confirmation_index(identity_confirmations, (), claim_id=context.claim_id,
                                      input_revision=context.input_revision))
    return build_groups(observations, index, context=context, lineage=lineage)
