"""Fixture stage producers and the seed claims. Deliberately unrelated to uploaded pixels.

Each producer stands in for one module worker (M1 to M6). It consumes the real command,
runs through the shared consumer runtime, and stores the validated contract records of
``claim_cmev.contracts.fixtures`` for the claim and input revision, all with
``provenance.source_kind = "fixture"``. No producer opens an uploaded file: the scenario
is chosen deterministically per claim, and every event and job result says the uploaded
pixels were not read. The pen-mark producer stores what a detector emits (every mark
``pending``); a scenario's pre-decided marks become recorded fixture-surveyor actions that
the consolidator applies through M6, exactly like a surveyor's decision.

Seed claims receive their assessments from the same pipeline code (orchestrator, these
producers and M8 over the fixture bundles), never from hand-written results.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
import hashlib
from typing import Any

from sqlalchemy.orm import Session

from .contracts.common import deterministic_id
from .contracts.documents import DocumentPage, PenMark, with_effective_price
from .contracts.fixtures import SCENARIOS, VERSIONS, FixtureBundle, fixture_bundle
from .messaging.consumer import Context, PermanentError
from .messaging.outbox import build_message
from .orchestration.plan import COMMAND_TOPIC, EVENT_TOPIC, FIXTURE_FAMILY, SERVICE, VersionBundle
from .persistence.store import insert_records
from .runtime import get, put, utcnow

FIXTURE_NOTICE = ("Demonstration fixtures only. Branch records come from hand-authored contract fixtures and the "
                  "uploaded pixels were not read; the M8 rules ran over those fixtures with a synthetic cost table. "
                  "Not model results, repair-price validation or a final claim approval.")
NO_PIXELS = "uploaded-pixels:not-read"
SEED_MARKER = "seed:pipeline-v2"
SEEDS = (  # reference, policy, surveyor, make, model, year, plate, workshop, loss date, scenario, phase
    ("CLM-24019", "MOT/2026/884213", "R. Miguel", "Toyota", "Corolla Altis 1.6", 2019, "SJB 4412 K",
     "Northbridge Autoworks", "2026-09-09", "pending_price_change", "drain"),
    ("CLM-24020", "MOT/2026/884977", "L. Tan", "Honda", "City 1.5", 2019, "SGA 9087 M", "Kallang Panel & Paint",
     "2026-09-12", "exclusion_and_supported", "worker"),
    ("CLM-24021", "MOT/2026/885104", "P. Sharma", "Mazda", "CX-5 2.0", 2022, "SLK 2210 B",
     "Westgate Motor Services", "2026-09-15", "partial_extraction", "upload"),
    ("CLM-24018", "MOT/2026/883760", "A. Fernandez", "Hyundai", "Tucson 1.6T", 2020, "SDF 7741 X",
     "Northbridge Autoworks", "2026-09-02", "unphotographed_part", "finalize"),
)
SEED_IDS = {spec[0]: f"01K5000000000000000000000{i}" for i, spec in enumerate(SEEDS)}


def scenario_for(claim: Mapping[str, Any]) -> str:
    """The claim's recorded scenario, else a deterministic choice from its claim ID."""
    chosen = claim.get("fixture_scenario")
    if chosen in SCENARIOS:
        return chosen
    return SCENARIOS[int(hashlib.sha256(claim["claim_id"].encode()).hexdigest(), 16) % len(SCENARIOS)]


@lru_cache(maxsize=64)
def _bundle(scenario: str, claim_id: str, input_revision: int) -> FixtureBundle:
    return fixture_bundle(scenario, claim_id, input_revision)


def _mask(ref: Any, *, component: bool = False) -> dict[str, Any]:
    data = {"artifact_id": ref.artifact_id, "object_uri": ref.object_uri, "sha256": ref.sha256, "width": ref.width,
            "height": ref.height, "encoding": ref.encoding}
    if component and ref.component_index is not None:
        data["component_index"] = ref.component_index
    return data


def _placeholder_mask(claim_id: str, rev: int, name: str) -> dict[str, Any]:
    uri = f"s3://cmev-derived/fixture/{claim_id}/{rev}/{name}"
    return {"artifact_id": deterministic_id("art", uri), "object_uri": uri,
            "sha256": hashlib.sha256(uri.encode()).hexdigest(), "width": 512, "height": 512,
            "encoding": "class_index_png"}


class FixtureProducers:
    """One consumer group per stage worker, each backed by the claim's fixture scenario."""

    def __init__(self, versions: VersionBundle):
        self.versions = versions

    def groups(self) -> dict[str, dict[str, Callable[[Context], Mapping[str, Any]]]]:
        handlers = {"parts": self.parts, "damage": self.damage, "summary": self.summary,
                    "page_read": self.page_read, "line_items": self.line_items, "pen_marks": self.pen_marks}
        return {SERVICE[stage]: {COMMAND_TOPIC[stage]: handler} for stage, handler in handlers.items()}

    # ------------------------------------------------------------------ shared
    def _load(self, ctx: Context, stage: str) -> tuple[FixtureBundle, dict[str, Any], str]:
        env = ctx.envelope
        if dict(env.versions) != self.versions.for_stage(stage):
            raise PermanentError("fixture_version_unsupported",
                                 "fixture producers only honour the fixture version tags")
        claim = get(ctx.session, "claim:" + env.claim_id)
        claim_input = get(ctx.session, f"input:{env.claim_id}:{env.input_revision}")
        if claim is None or claim_input is None:
            raise PermanentError("claim_input_missing", "claim or input revision record not found")
        scenario = claim_input.get("fixture_scenario") or scenario_for(claim)
        return _bundle(scenario, env.claim_id, env.input_revision), claim_input, scenario

    def _store(self, ctx: Context, stage: str, records: list[tuple[str, Any]]) -> list[str]:
        env = ctx.envelope
        return insert_records(ctx.session, records, claim_id=env.claim_id, input_revision=env.input_revision,
                              stage=stage, job_key=env.job_key, now=ctx.now)

    def _emit(self, ctx: Context, stage: str, scenario: str, payload: Mapping[str, Any]) -> None:
        env, job = ctx.envelope, ctx.job or {}
        message = build_message(
            EVENT_TOPIC[stage], claim_id=env.claim_id, input_revision=env.input_revision, task=job["task"],
            versions=env.versions, target=job["target"], attempt_epoch=job["attempt_epoch"], trace_id=env.trace_id,
            occurred_at=ctx.now, payload=payload, causation_id=env.dedup_key,
            provenance=ctx.provenance(source_dataset_id=f"fixture:{scenario}",
                                      derivation_refs=[f"fixture-bundle:{scenario}", NO_PIXELS]))
        ctx.emit(EVENT_TOPIC[stage], message)

    @staticmethod
    def _mapped(items: list[Any], targets: list[str], target: str) -> list[Any]:
        """Fixture items this item's job emits: bundle item j goes to the job of item ``j mod n``."""
        if target not in targets:
            raise PermanentError("target_not_in_revision", "the command names an item outside its input revision")
        index = targets.index(target)
        return [item for j, item in enumerate(items) if j % len(targets) == index]

    # ------------------------------------------------------------------ image branch
    def parts(self, ctx: Context) -> dict[str, Any]:
        bundle, claim_input, scenario = self._load(ctx, "parts")
        target = ctx.job["target"]
        photos = [f.file_id for f in bundle.files if f.kind == "photo"]
        mapped = set(self._mapped(photos, claim_input["photo_ids"], target))
        quality = [q for q in bundle.image_quality if q.photo_id in mapped]
        predictions = [p for p in bundle.part_predictions if p.photo_id in mapped]
        self._store(ctx, "parts", [("image_quality", q) for q in quality] + [("part_prediction", p) for p in predictions])
        mask = _mask(predictions[0].mask_ref) if predictions else _placeholder_mask(
            ctx.envelope.claim_id, ctx.envelope.input_revision, f"parts_{target}.png")
        transform = (predictions[0].transform if predictions else bundle.part_predictions[0].transform
                     ).model_dump(mode="json")
        first = quality[0] if quality else None
        payload = {"photo_id": target, "part_mask_ref": mask, "transform": transform,
                   "parts": [{"part_code": p.part_code, "pixel_count": p.pixel_count,
                              "mean_confidence": p.mean_confidence} for p in predictions],
                   "part_prediction_ids": [p.prediction_id for p in predictions],
                   "image_quality": {"state": first.state if first else "not_assessed",
                                     "blur_score": first.blur_score if first else None,
                                     "exposure_state": first.exposure_state if first else "not_assessed",
                                     "reasons": list(first.reasons) if first else ["fixture_no_photo_mapped"]},
                   "empty_result": not predictions}
        self._emit(ctx, "parts", scenario, payload)
        return {"photo_id": target, "part_prediction_ids": payload["part_prediction_ids"], "part_mask_ref": mask,
                "transform": transform, "fixture_photo_ids": sorted(mapped), "notice": NO_PIXELS}

    def damage(self, ctx: Context) -> dict[str, Any]:
        bundle, claim_input, scenario = self._load(ctx, "damage")
        target = ctx.job["target"]
        photos = [f.file_id for f in bundle.files if f.kind == "photo"]
        mapped = set(self._mapped(photos, claim_input["photo_ids"], target))
        observations = [o for o in bundle.observations if o.photo_id in mapped]
        self._store(ctx, "damage", [("damage_observation", o) for o in observations])
        mask = (_mask(observations[0].damage_mask_ref) if observations else
                _placeholder_mask(ctx.envelope.claim_id, ctx.envelope.input_revision, f"damage_{target}.png"))
        payload = {"photo_id": target, "damage_mask_ref": mask, "observations": [{
            "observation_id": o.observation_id, "photo_id": o.photo_id, "damage_type": o.damage_code,
            "damage_confidence": o.damage_confidence, "assignment_status": o.assignment_status,
            "part_code": o.part_code, "part_reason": o.part_reason, "side": o.side, "side_reason": o.side_reason,
            "candidates": [c.model_dump(mode="json") for c in o.candidates],
            "primary_containment": o.primary_containment, "runner_up_containment": o.runner_up_containment,
            "background_containment": o.background_containment, "area_pixels": o.area_pixels,
            "area_fraction": o.area_fraction, "bbox_norm": list(o.bbox_norm),
            "damage_mask_ref": {"artifact_id": o.damage_mask_ref.artifact_id,
                                **({"component_index": o.damage_mask_ref.component_index}
                                   if o.damage_mask_ref.component_index else {})},
            "part_mask_ref": {"artifact_id": o.part_mask_ref.artifact_id} if o.part_mask_ref else None,
            "assignment_config_version": o.assignment_config_version} for o in observations],
            "observation_ids": [o.observation_id for o in observations], "dropped_region_count": 0,
            "unknown_part_count": sum(1 for o in observations if o.part_code is None),
            "empty_result": not observations}
        self._emit(ctx, "damage", scenario, payload)
        return {"photo_id": target, "observation_ids": payload["observation_ids"], "notice": NO_PIXELS}

    def summary(self, ctx: Context) -> dict[str, Any]:
        bundle, claim_input, scenario = self._load(ctx, "summary")
        from .orchestration.corrections import review_events
        if any(e.action_type in ("confirm_identity", "confirm_coverage") for e in review_events(claim_input)):
            from .orchestration import state
            from .orchestration.review_summary import corrected_summary
            jobs = state.effective_jobs(ctx.session, ctx.envelope.claim_id, ctx.envelope.input_revision, "parts")
            source_revision = min(j["input_revision"] for j in jobs)
            baseline = _bundle(scenario, ctx.envelope.claim_id, source_revision)
            outcome, identities, coverage = corrected_summary(ctx, claim_input, baseline)
            records = ([("part_summary", s) for s in outcome.summaries] +
                       [("part_coverage", c) for c in outcome.coverage] +
                       [("identity_confirmation", c) for c in identities] +
                       [("coverage_confirmation", c) for c in coverage])
            self._store(ctx, "summary", records)
            payload = outcome.event_payload()
            self._emit(ctx, "summary", scenario, payload)
            return {**payload, "notice": NO_PIXELS, "human_confirmation_rerun": True}
        records = ([("part_summary", s) for s in bundle.part_summaries] +
                   [("part_coverage", c) for c in bundle.coverage] +
                   [("identity_confirmation", c) for c in bundle.identity_confirmations] +
                   [("coverage_confirmation", c) for c in bundle.coverage_confirmations])
        self._store(ctx, "summary", records)
        counts = {k: sum(1 for c in bundle.coverage if c.state == k)
                  for k in ("adequate", "inadequate", "not_visible", "unresolved")}
        payload = {"summary_ids": [s.summary_id for s in bundle.part_summaries],
                   "coverage_ids": [c.coverage_id for c in bundle.coverage],
                   "unresolved_observation_ids": [m for s in bundle.part_summaries if s.identity_status == "unresolved"
                                                  for m in s.member_observation_ids],
                   "coverage_counts": counts, "branch": "image"}
        self._emit(ctx, "summary", scenario, payload)
        return {"summary_ids": payload["summary_ids"], "coverage_ids": payload["coverage_ids"],
                "fixture_confirmations": len(bundle.identity_confirmations) + len(bundle.coverage_confirmations),
                "notice": NO_PIXELS}

    # ------------------------------------------------------------------ document branch
    def page_read(self, ctx: Context) -> dict[str, Any]:
        bundle, claim_input, scenario = self._load(ctx, "page_read")
        target = ctx.job["target"]
        mapped = self._mapped(list(bundle.pages), claim_input["page_targets"], target)
        command_page = ctx.job["command"]["payload"]["page"] if ctx.job.get("command") else {}
        if mapped:
            page = mapped[0]
        else:  # an uploaded page with no fixture counterpart: recorded as unreadable, never invented
            base = bundle.pages[0].model_dump()
            base.update(page_id=deterministic_id("dp", ctx.envelope.job_key, 0),
                        file_id=command_page.get("file_id", target), page_number=command_page.get("page_number") or 1,
                        text_boxes=[], mean_text_confidence=None, mean_text_confidence_reason="fixture_page_not_modelled",
                        quality={"state": "unreadable", "reasons": ["fixture_page_not_modelled"]})
            page = DocumentPage.model_validate(base)
        self._store(ctx, "page_read", [("document_page", page)])
        reading = page.page_reading_ref.model_dump(mode="json")
        render = page.corrected_render_ref.model_dump(mode="json")
        transform = page.transform.model_dump(mode="json")
        payload = {"page_id": page.page_id, "page_number": page.page_number, "corrected_render_ref": render,
                   "page_reading_ref": reading, "transform": transform, "text_granularity": page.text_granularity,
                   "token_count": len(page.text_boxes), "mean_text_confidence": page.mean_text_confidence,
                   "page_quality": {"state": page.quality.state, "reasons": list(page.quality.reasons)},
                   "correction_applied": page.transform.geometry_correction != "none"}
        self._emit(ctx, "page_read", scenario, payload)
        return {"page_id": page.page_id, "page_number": page.page_number, "corrected_render_ref": render,
                "page_reading_ref": reading, "transform": transform, "notice": NO_PIXELS}

    def line_items(self, ctx: Context) -> dict[str, Any]:
        bundle, _claim_input, scenario = self._load(ctx, "line_items")
        # M5 output never knows the pen marks: effective prices are derived later, from decided marks.
        items = [with_effective_price(item, []) for item in bundle.line_items]
        from .orchestration import state
        from .persistence.store import load_records
        page_jobs = state.effective_jobs(ctx.session, ctx.envelope.claim_id, ctx.envelope.input_revision, "page_read")
        pages = load_records(ctx.session, ctx.envelope.claim_id, [j["job_key"] for j in page_jobs]).get("document_page", [])
        declaration = bundle.declaration
        unreadable = [p for p in pages if p.quality.state == "unreadable"]
        partial = [p for p in pages if p.quality.state == "partial"]
        if unreadable or partial:
            data = declaration.model_dump()
            data.update(state="unreadable" if len(unreadable) == len(pages) else "partial",
                        reasons=list(dict.fromkeys([*declaration.reasons, "page_unreadable" if unreadable else "page_partial"])),
                        unparsed_region_count=declaration.unparsed_region_count + len(unreadable) + len(partial),
                        pages_covered=[p.page_id for p in pages if p.quality.state == "complete"])
            declaration = type(declaration).model_validate(data)
        self._store(ctx, "line_items", [("line_item", i) for i in items] +
                    [("declaration_completeness", declaration)])
        payload = {"line_item_ids": [i.entry_id for i in items], "line_items": [{
            "entry_id": i.entry_id, "page_id": i.page_id, "row_box_norm": list(i.row_box_norm),
            "part_code": i.part_code, "part_mapping_status": i.part_mapping_status, "side": i.side,
            "operation": i.operation, "operation_mapping_status": i.operation_mapping_status, "quantity": i.quantity,
            "unit_price": i.unit_price, "printed_line_amount": i.printed_line_amount, "currency": i.currency,
            "cost_basis": i.cost_basis, "field_uncertainty": [u.model_dump(mode="json") for u in i.field_uncertainty]}
            for i in items], "layout_family": declaration.layout_family,
            "declaration_completeness": declaration.state, "completeness_reasons": list(declaration.reasons),
            "unparsed_region_count": declaration.unparsed_region_count, "engine": "parser",
            "branch_stage": "line_items"}
        self._emit(ctx, "line_items", scenario, payload)
        rows = [{"entry_id": i.entry_id, "page_id": i.page_id, "row_box_norm": list(i.row_box_norm),
                 "amount_box_norm": list(i.amount_box_norm) if i.amount_box_norm else None} for i in items]
        return {"line_item_ids": payload["line_item_ids"], "row_boxes": rows, "notice": NO_PIXELS}

    def pen_marks(self, ctx: Context) -> dict[str, Any]:
        bundle, _claim_input, scenario = self._load(ctx, "pen_marks")
        machine, actions = [], []
        for mark in bundle.pen_marks:
            data = mark.model_dump()
            data.update(state="pending", confirmed_amount=None, confirmed_currency=None, confirmed_cost_basis=None,
                        decision_action_id=None, decided_by=None, decided_at=None, review_revision=None)
            machine.append(PenMark.model_validate(data))
            if mark.state != "pending":
                actions.append({"action_type": "confirm_mark" if mark.state == "confirmed" else "reject_mark",
                                "action_id": mark.decision_action_id, "mark_id": mark.mark_id,
                                "confirmed_amount": mark.confirmed_amount,
                                "confirmed_currency": mark.confirmed_currency,
                                "confirmed_cost_basis": mark.confirmed_cost_basis, "actor": mark.decided_by,
                                "recorded_at": mark.decided_at.isoformat(), "review_revision": mark.review_revision,
                                "source": "fixture_surveyor", "kind": "mark_action"})
        self._store(ctx, "pen_marks", [("pen_mark", m) for m in machine] +
                    [("fixture_mark_action", a) for a in actions])
        conflicting = {m.entry_id for m in machine if m.entry_id and
                       sum(1 for n in machine if n.entry_id == m.entry_id) > 1}
        payload = {"mark_ids": [m.mark_id for m in machine], "marks": [{
            "mark_id": m.mark_id, "page_id": m.page_id, "mark_type": m.mark_type, "box_norm": list(m.box_norm),
            "detection_confidence": m.detection_confidence, "entry_id": m.entry_id,
            "candidate_entry_ids": list(m.candidate_entry_ids), "link_reason": m.link_reason, "state": "pending",
            "trocr_suggestion": None} for m in machine],
            "unlinked_count": sum(1 for m in machine if m.entry_id is None),
            "conflicting_count": len(conflicting), "branch": "document"}
        self._emit(ctx, "pen_marks", scenario, payload)
        return {"mark_ids": payload["mark_ids"], "fixture_mark_actions": len(actions), "notice": NO_PIXELS}


# ------------------------------------------------------------------ seeds
def _seed_claim(index: int, spec: tuple) -> dict[str, Any]:
    ref, policy, surveyor, make, model, year, plate, workshop, loss, scenario, _phase = spec
    cid = SEED_IDS[ref]
    bundle = _bundle(scenario, cid, 1)
    return {"claim_id": cid, "reference": ref, "policy_number": policy, "surveyor": surveyor,
            "owner_id": "demo-surveyor", "vehicle": {"make": make, "model": model, "year": year, "plate": plate,
                                                    "vehicle_class": bundle.vehicle_class},
            "workshop": workshop, "loss_date": loss, "status": "awaiting_upload", "photograph_count": 0,
            "currency": "SGD", "input_revision": 0, "review_revision": 0, "source_kind": "fixture",
            "fixture_scenario": scenario, "created_at": utcnow().isoformat()}


def seed_files(db: Session, claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Placeholder file records for the scenario's fixture originals: no bytes exist for them."""
    bundle = _bundle(claim["fixture_scenario"], claim["claim_id"], 1)
    files = []
    for f in bundle.files:
        item = {"file_id": f.file_id, "claim_id": claim["claim_id"], "original_name": f.original_name,
                "media_type": f.media_type, "byte_count": f.byte_count, "sha256": f.sha256,
                "role": "photograph" if f.kind == "photo" else "estimate_page", "page_count": 1, "width": f.width,
                "height": f.height, "exif_orientation": f.exif_orientation, "object_uri": f.object_ref.object_uri,
                "state": "committed", "url": None, "fixture_placeholder": True}
        put(db, "file:" + f.file_id, "file", item, claim["claim_id"])
        files.append(item)
    return files


def seed(database: Any, *, commit: Callable[[Session, dict[str, Any], list[dict[str, Any]]], None],
         drain: Callable[[], Any], finalize: Callable[[Session, str], None]) -> bool:
    """Create the four demonstration claims once and run their inputs through the pipeline.

    ``commit`` commits an input revision, ``drain`` runs the in-process pipeline for the
    claims committed so far and ``finalize`` freezes a seed's review. CLM-24020 is left
    queued for the worker; CLM-24021 waits for an upload.
    """
    with database.session.begin() as db:
        if get(db, SEED_MARKER):
            return False
        claims = [_seed_claim(i, spec) for i, spec in enumerate(SEEDS)]
        for claim, spec in zip(claims, SEEDS):
            put(db, "claim:" + claim["claim_id"], "claim", claim, claim["claim_id"])
            if spec[-1] in ("drain", "finalize"):
                commit(db, claim, seed_files(db, claim))
        put(db, SEED_MARKER, "seed", {"created_at": utcnow().isoformat(), "scenarios": dict(
            (spec[0], spec[9]) for spec in SEEDS)})
    drain()
    with database.session.begin() as db:
        for claim, spec in zip(claims, SEEDS):
            if spec[-1] == "finalize":
                finalize(db, claim["claim_id"])
            elif spec[-1] == "worker":
                current = get(db, "claim:" + claim["claim_id"])
                commit(db, dict(current), seed_files(db, current))
    return True


__all__ = ["FIXTURE_NOTICE", "FixtureProducers", "NO_PIXELS", "SEEDS", "SEED_MARKER", "scenario_for", "seed",
           "seed_files"]
