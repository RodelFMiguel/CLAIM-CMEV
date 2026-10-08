"""M3 serving: the adapter entry point and the Kafka handler over real M1 and M2 output.

Specification: docs/specs/module-03-part-summary-coverage.md

The summary command names record IDs only. The handler reads the M1 part predictions and
the M2 damage observations they name from the database, the surveyor's confirmations from
the input revision, and the photographs and M1 masks from the object store.
``run_part_summary`` then measures the screening signals on those pixels and calls the
deterministic ``summarise_parts``. It loads no weights and writes nothing.

After a surveyor's confirmation the orchestrator reruns only this stage for the new input
revision. The same handler serves that run: it reads the earlier revision's rows and
artifacts through the reuse lineage, so no model runs again.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import io
from typing import Any

import numpy as np
from PIL import Image

from ...contracts.common import ContractError, Provenance
from ...contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    PartPrediction,
)
from ...messaging.consumer import Context, PermanentError
from ...messaging.outbox import build_message
from ...orchestration import state
from ...orchestration.corrections import review_events
from ...orchestration.review_summary import human_confirmations
from ...persistence.store import insert_records, load_records
from ...runtime import get
from ..artifacts import read_object
from ..frame import build_model_frame, same_frame
from ..palette import ID_TO_PART_CODE
from .config import SummaryConfig, load_summary_config
from .confirmations import SummaryContext
from .screening import compute_view_signals
from .summary import PartSummaryOutcome, summarise_parts

SUMMARY_EVENT_TOPIC = "cmev.evt.part-summarised.v1"
_PART_ID = {code: class_id for class_id, code in ID_TO_PART_CODE.items()}


@dataclass(frozen=True)
class PartSummaryRequest:
    """Everything M3 reads for one input revision. ``photos`` and ``part_masks`` are keyed by photo ID."""

    context: SummaryContext
    observations: Sequence[ImageDamageObservation]
    part_predictions: Sequence[PartPrediction]
    identity_confirmations: Sequence[IdentityConfirmation] = ()
    coverage_confirmations: Sequence[CoverageConfirmation] = ()
    photos: Mapping[str, bytes] = field(default_factory=dict, repr=False)
    part_masks: Mapping[str, bytes] = field(default_factory=dict, repr=False)
    photo_ids: Iterable[str] | None = None


def measure_view_signals(part_predictions: Sequence[PartPrediction], photos: Mapping[str, bytes],
                         part_masks: Mapping[str, bytes]) -> dict[tuple[str, str], dict[str, float]]:
    """The screening signals of every accepted part on every photograph, measured on the pixels.

    The photograph is rebuilt in the model frame M1 recorded, so its pixels and the M1 mask
    share one grid. A photograph or mask that is missing leaves its views without signals;
    the screen then reports them ``not_run``, never a pass.
    """
    signals: dict[tuple[str, str], dict[str, float]] = {}
    by_photo: dict[str, list[PartPrediction]] = {}
    for prediction in part_predictions:
        if prediction.accepted:
            by_photo.setdefault(prediction.photo_id, []).append(prediction)
    for photo_id, accepted in by_photo.items():
        if photo_id not in photos or photo_id not in part_masks:
            continue
        ref, recorded = accepted[0].mask_ref, accepted[0].transform
        if hashlib.sha256(part_masks[photo_id]).hexdigest() != ref.sha256:
            raise ContractError("artifact_hash_mismatch", f"the part mask of {photo_id} is not the one its rows name")
        mask = np.array(Image.open(io.BytesIO(part_masks[photo_id])))
        frame = build_model_frame(photos[photo_id], (ref.width, ref.height), "longest_edge_pad")
        if not same_frame(frame.transform, recorded):
            raise ContractError("transform_mismatch", f"photo {photo_id} does not rebuild the frame its part mask "
                                                      "was made in")
        for prediction in accepted:
            signals[(photo_id, prediction.part_code)] = compute_view_signals(
                mask, _PART_ID[prediction.part_code], frame.pixels, content_box=frame.plan.content_box())
    return signals


def run_part_summary(request: PartSummaryRequest, config: SummaryConfig | None = None) -> PartSummaryOutcome:
    """Adapter entry point: group the observations and decide coverage for one input revision.

    Nothing is written here; the caller persists the records and publishes the event
    (module-03, "Adapter entry point").
    """
    config = config or load_summary_config()
    signals = measure_view_signals(request.part_predictions, request.photos, request.part_masks)
    return summarise_parts(
        request.observations, request.part_predictions, request.identity_confirmations,
        request.coverage_confirmations, context=request.context, config=config, view_signals=signals,
        photo_ids=request.photo_ids)


def make_summary_handler(storage: Any, versions: Mapping[str, str], config: SummaryConfig | None = None):
    """Factory creating a ConsumerRuntime handler for ``cmev.cmd.part-summary.v1`` over real M1/M2 rows.

    ``versions`` are the summary stage versions this worker serves; a command pinned to
    anything else, such as the fixture tags, is refused.
    """
    served = dict(versions)
    config = config or load_summary_config()

    def handler(ctx: Context) -> dict[str, Any]:
        env, payload, session = ctx.envelope, ctx.payload, ctx.session
        if dict(env.versions) != served:
            raise PermanentError("summary_version_unsupported",
                                 "this worker serves only commands pinned to the versions it loaded")
        claim_input = get(session, f"input:{env.claim_id}:{env.input_revision}")
        if claim_input is None:
            raise PermanentError("claim_input_missing", "the input revision record was not found")

        # The rows the command names: the effective parts and damage jobs, following reuse lineage.
        parts_jobs = state.effective_jobs(session, env.claim_id, env.input_revision, "parts")
        damage_jobs = state.effective_jobs(session, env.claim_id, env.input_revision, "damage")
        records = load_records(session, env.claim_id, [j["job_key"] for j in (*parts_jobs, *damage_jobs)])
        predictions = records.get("part_prediction", [])
        observations = records.get("damage_observation", [])
        if (sorted(p.prediction_id for p in predictions) != sorted(payload["part_prediction_ids"])
                or sorted(o.observation_id for o in observations) != sorted(payload["observation_ids"])):
            raise PermanentError("upstream_records_mismatch",
                                 "the stored part and damage rows are not the ones the command names")

        needed = {p.photo_id for p in predictions if p.accepted}
        photo_refs = {j["target"]: j["command"]["payload"]["photo"] for j in parts_jobs}
        photos = {photo_id: read_object(storage, photo_refs[photo_id]["object_uri"], f"photo {photo_id}")
                  for photo_id in sorted(needed)}
        mask_uris = {p.photo_id: p.mask_ref.object_uri for p in predictions if p.accepted}
        masks = {photo_id: read_object(storage, uri, f"the part mask of {photo_id}")
                 for photo_id, uri in sorted(mask_uris.items())}

        identities, coverage_confirmations = human_confirmations(ctx, claim_input)
        source_revisions = {r.input_revision for r in (*predictions, *observations)}
        reuse = min(source_revisions) if source_revisions and min(source_revisions) < env.input_revision else None
        actions = [event.action_id for event in review_events(claim_input)]
        context = SummaryContext(
            claim_id=env.claim_id, input_revision=env.input_revision, job_key=env.job_key, versions=dict(env.versions),
            provenance=Provenance(**ctx.provenance(derivation_refs=actions)), reuse_from_input_revision=reuse)
        try:
            outcome = run_part_summary(PartSummaryRequest(
                context=context, observations=observations, part_predictions=predictions,
                identity_confirmations=identities, coverage_confirmations=coverage_confirmations, photos=photos,
                part_masks=masks, photo_ids=sorted(set(payload["photo_ids"]) | needed)), config)
        except ContractError as exc:
            raise PermanentError(exc.reason_code, exc.message) from exc

        insert_records(session, [*(("part_summary", s) for s in outcome.summaries),
                                 *(("part_coverage", c) for c in outcome.coverage),
                                 *(("identity_confirmation", c) for c in identities),
                                 *(("coverage_confirmation", c) for c in coverage_confirmations)],
                       claim_id=env.claim_id, input_revision=env.input_revision, stage="summary", job_key=env.job_key,
                       now=ctx.now)
        event_payload = outcome.event_payload()
        job = ctx.job or {}
        ctx.emit(SUMMARY_EVENT_TOPIC, build_message(
            SUMMARY_EVENT_TOPIC, claim_id=env.claim_id, input_revision=env.input_revision, task=job["task"],
            versions=env.versions, target=job["target"], attempt_epoch=job["attempt_epoch"], trace_id=env.trace_id,
            occurred_at=ctx.now, payload=event_payload, causation_id=env.dedup_key,
            provenance=ctx.provenance(derivation_refs=actions)))
        return {**event_payload, "reuse_from_input_revision": reuse,
                "confirmation_ids": list(outcome.confirmation_ids),
                "views_measured": len(masks), "processing_status": outcome.processing_status}

    return handler


__all__ = ["PartSummaryRequest", "SUMMARY_EVENT_TOPIC", "make_summary_handler", "measure_view_signals",
           "run_part_summary"]
