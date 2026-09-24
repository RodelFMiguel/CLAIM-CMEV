"""``summarise_parts``: grouping plus coverage for one input revision (pure).

This is the deterministic core that the future ``run_part_summary`` adapter and
``cmev-worker-summary`` consumer shell will call; it reads no pixels, loads no weights
and never publishes. It returns the records, the ``cmev.evt.part-summarised.v1``
payload fields and the confirmation data the orchestrator folds into the job key.

After a human confirmation creates a new input revision, the orchestrator reruns only
this step with ``reuse_from_input_revision``: the earlier revision's observations and
predictions are reused, the new rows carry the reuse lineage in
``provenance.derivation_refs``, and the earlier revision's rows are left untouched
(records are immutable and their IDs derive from their own job key).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ...contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    PartCoverage,
    PartPrediction,
    PartSummary,
)
from .config import SummaryConfig
from .confirmations import ReuseLineage, SummaryContext, build_confirmation_index, check_inputs
from .coverage import COVERAGE_STATES, BranchStatus, build_coverage
from .grouping import build_groups

_PROCESSING_STATUS = {"succeeded": "succeeded", "partial": "partial", "failed": "failed",
                      "skipped_no_photos": "succeeded"}


@dataclass(frozen=True)
class PartSummaryOutcome:
    processing_status: Literal["succeeded", "partial", "failed"]
    """Mirrors the upstream image branch; ``failed`` is never an empty success."""
    summaries: tuple[PartSummary, ...]
    coverage: tuple[PartCoverage, ...]
    unresolved_observation_ids: tuple[str, ...]
    """Observations without a confirmed physical identity (``part_only`` and ``unresolved``)."""
    confirmation_ids: tuple[str, ...]
    """Every supplied confirmation ID: the set the job key must include."""
    contributing_confirmation_ids: tuple[str, ...]
    superseded_confirmation_ids: tuple[str, ...]
    confirmation_set_signature: str
    reuse: ReuseLineage | None
    reasons: tuple[str, ...]

    @property
    def summary_ids(self) -> tuple[str, ...]:
        return tuple(s.summary_id for s in self.summaries)

    @property
    def coverage_ids(self) -> tuple[str, ...]:
        return tuple(c.coverage_id for c in self.coverage)

    @property
    def coverage_counts(self) -> dict[str, int]:
        return {state: sum(c.state == state for c in self.coverage) for state in COVERAGE_STATES}

    def event_payload(self) -> dict[str, Any]:
        """The ``cmev.evt.part-summarised.v1`` payload."""
        return {"summary_ids": list(self.summary_ids), "coverage_ids": list(self.coverage_ids),
                "unresolved_observation_ids": list(self.unresolved_observation_ids),
                "coverage_counts": self.coverage_counts, "branch": "image"}


def summarise_parts(
    observations: Sequence[ImageDamageObservation],
    part_predictions: Sequence[PartPrediction],
    identity_confirmations: Sequence[IdentityConfirmation] = (),
    coverage_confirmations: Sequence[CoverageConfirmation] = (),
    *,
    context: SummaryContext,
    config: SummaryConfig,
    supported_parts: Sequence[str] | None = None,
    view_signals: Mapping[tuple[str, str], Mapping[str, float]] | None = None,
    branch_status: BranchStatus = "succeeded",
    failed_photo_ids: Iterable[str] = (),
    photo_ids: Iterable[str] | None = None,
) -> PartSummaryOutcome:
    """Group observations and decide coverage for every slot of one input revision."""
    lineage = check_inputs(context, config.config_version, observations, part_predictions)
    index = build_confirmation_index(identity_confirmations, coverage_confirmations, claim_id=context.claim_id,
                                     input_revision=context.input_revision, photo_ids=photo_ids)
    summaries = build_groups(observations, index, context=context, lineage=lineage)
    failed_photos = tuple(sorted(set(failed_photo_ids)))
    coverage = build_coverage(part_predictions, observations, index, context=context, config=config, lineage=lineage,
                              supported_parts=supported_parts if supported_parts is not None else
                              config.supported_parts(), view_signals=view_signals, branch_status=branch_status,
                              failed_photo_ids=failed_photos)
    unresolved = tuple(m for s in summaries if s.identity_status != "resolved" for m in s.member_observation_ids)
    reasons = {"failed": ("processing_failed",), "partial": ("processing_failed",),
               "skipped_no_photos": ("no_photographs",)}.get(branch_status, ())
    return PartSummaryOutcome(
        processing_status=_PROCESSING_STATUS[branch_status], summaries=tuple(summaries), coverage=tuple(coverage),
        unresolved_observation_ids=unresolved, confirmation_ids=index.supplied_ids,
        contributing_confirmation_ids=index.contributing_ids, superseded_confirmation_ids=index.superseded_ids,
        confirmation_set_signature=index.signature, reuse=lineage, reasons=reasons)
