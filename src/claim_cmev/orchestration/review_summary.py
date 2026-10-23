"""Run M3 over reused fixture M1/M2 records after an explicit human confirmation."""
from ..contracts.common import Provenance, deterministic_id
from ..contracts.imaging import IdentityConfirmation, CoverageConfirmation
from ..persistence.store import load_records
from ..vision.multiview.config import load_summary_config
from ..vision.multiview.summary import summarise_parts, SummaryContext
from . import state
from .corrections import review_events


def human_confirmations(ctx, claim_input):
    """The surveyor's identity and coverage confirmations recorded on this input revision, as M3 records.

    One identity record per named photograph. Their IDs derive from the summary job and the
    review action, so a replay reproduces them.
    """
    identities, coverage = [], []
    for event in review_events(claim_input):
        values = event.new_values
        if event.action_type not in ("confirm_identity", "confirm_coverage"):
            continue
        common = dict(claim_id=event.claim_id, input_revision=ctx.envelope.input_revision,
                      actor=event.actor, recorded_at=event.recorded_at, review_revision=event.resulting_review_revision,
                      note=event.note, part_code=values["part_code"], side=values["side"],
                      provenance=Provenance(source_kind="real", runtime_profile=ctx.profile,
                                            producer_service="cmev-api", derivation_refs=[event.action_id]))
        if event.action_type == "confirm_identity":
            for photo_id in values["photo_ids"]:
                identities.append(IdentityConfirmation(**common, photo_id=photo_id,
                    confirmation_id=deterministic_id("ic", ctx.envelope.job_key, event.action_id, photo_id)))
        else:
            coverage.append(CoverageConfirmation(**common,
                confirmation_id=deterministic_id("cc", ctx.envelope.job_key, event.action_id),
                covering_photo_ids=values["photo_ids"], covers_enough=values["covers_enough"],
                reason=values.get("reason")))
    return identities, coverage


def corrected_summary(ctx, claim_input, baseline):
    records = {}
    for stage in ("parts", "damage"):
        jobs = state.effective_jobs(ctx.session, ctx.envelope.claim_id, ctx.envelope.input_revision, stage)
        for kind, rows in load_records(ctx.session, ctx.envelope.claim_id, [j["job_key"] for j in jobs]).items():
            records.setdefault(kind, []).extend(rows)
    predictions = records.get("part_prediction", [])
    observations = records.get("damage_observation", [])
    known_photos = {p.photo_id for p in predictions} | {o.photo_id for o in observations}

    # Map human confirmations if upstream records use fixture photo IDs (ph-...)
    # while the surveyor's review actions named uploaded photo IDs.
    claim_photos = claim_input.get("photo_ids", [])
    bundle_photos = [f.file_id for f in baseline.files if f.kind == "photo"]
    photo_map = {cp: bundle_photos[i % len(bundle_photos)] for i, cp in enumerate(claim_photos)} if claim_photos and bundle_photos else {}

    def materialize(record):
        return record.model_copy(update={
            "confirmation_id": deterministic_id("hc", ctx.envelope.job_key, record.confirmation_id),
            "input_revision": ctx.envelope.input_revision,
            "provenance": record.provenance.model_copy(update={"derivation_refs":
                [*record.provenance.derivation_refs, record.confirmation_id]})})
    identities = [materialize(c) for c in baseline.identity_confirmations
                  if not known_photos or c.photo_id in known_photos]
    coverage = [materialize(c) for c in baseline.coverage_confirmations
                if not known_photos or set(c.covering_photo_ids).issubset(known_photos)]

    human_identities, human_coverage = human_confirmations(ctx, claim_input)
    if photo_map and known_photos:
        human_identities = [
            c.model_copy(update={"photo_id": photo_map.get(c.photo_id, c.photo_id)})
            if c.photo_id not in known_photos else c
            for c in human_identities
        ]
        human_coverage = [
            c.model_copy(update={"covering_photo_ids": [photo_map.get(p, p) for p in c.covering_photo_ids]})
            if not set(c.covering_photo_ids).issubset(known_photos) else c
            for c in human_coverage
        ]

    identities += human_identities
    coverage += human_coverage
    config = load_summary_config()
    versions = {**ctx.envelope.versions, "summary_config": config.config_version}
    source_revisions = {r.input_revision for r in [*predictions, *observations]}
    if len(source_revisions) > 1:
        raise ValueError("M3 reuse requires one coherent M1/M2 input revision")
    source_revision = next(iter(source_revisions), ctx.envelope.input_revision)
    context = SummaryContext(
        claim_id=ctx.envelope.claim_id, input_revision=ctx.envelope.input_revision,
        job_key=ctx.envelope.job_key, versions=versions,
        provenance=Provenance(**ctx.provenance(derivation_refs=[e.action_id for e in review_events(claim_input)])),
        reuse_from_input_revision=source_revision if source_revision < ctx.envelope.input_revision else None)
    view_signals = {(view.photo_id, slot.part_code): dict(view.signals)
                    for slot in baseline.coverage for view in slot.views}
    outcome = summarise_parts(observations, predictions, identities, coverage, context=context, config=config, view_signals=view_signals,
                              photo_ids=known_photos)
    return outcome, identities, coverage

