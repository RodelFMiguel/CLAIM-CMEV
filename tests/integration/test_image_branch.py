"""The whole image branch on real workers: M1 parts, M2 damage and M3 summary, one after another.

SQLite and the in-memory transport, as in the other runtime integration tests. Both
checkpoints are tiny random-weight SegFormers biased to answer one class everywhere, and the
photographs are noise, so these check connections, versions, provenance and what each stage
reads from the one before it. They say nothing about model accuracy, Kafka or Docker.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from sqlalchemy import select
import torch
from transformers import SegformerConfig, SegformerForSemanticSegmentation
import yaml

from claim_cmev import worker
from claim_cmev.contracts.common import deterministic_id, version_signature
from claim_cmev.fixtures import _bundle, seed_files
from claim_cmev.messaging import kafka
from claim_cmev.orchestration.consolidation import Consolidator
from claim_cmev.orchestration.intake import commit_input_revision
from claim_cmev.orchestration.plan import CODE_VERSION, STAGES, VersionBundle
from claim_cmev.orchestration.services import LocalPipeline, RuntimeSettings, build_runtimes
from claim_cmev.persistence.tables import assessments, jobs, stage_records
from claim_cmev.runtime import get, put, uid
from claim_cmev.storage import Storage
from claim_cmev.vision.damage.adapter import load_damage_segmenter, make_damage_handler
from claim_cmev.vision.damage.config import load_assignment_config
from claim_cmev.vision.damage.model_config import load_damage_model_config
from claim_cmev.vision.multiview.adapter import make_summary_handler
from claim_cmev.vision.multiview.config import load_summary_config
from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts.adapter import load_parts_segmenter, make_parts_handler
from claim_cmev.vision.parts.config import load_parts_config
from pipeline_helpers import Clock, table_version

SCENARIO = "exclusion_and_supported"
IMAGE_GROUPS = {"cmev-worker-parts", "cmev-worker-damage", "cmev-worker-summary"}
DOCUMENT_GROUPS = {"cmev-worker-ocr", "cmev-worker-lineitems", "cmev-worker-penmarks"}
TOPICS = {"cmev-worker-parts": "cmev.cmd.parts-segment.v1", "cmev-worker-damage": "cmev.cmd.damage-segment.v1",
          "cmev-worker-summary": "cmev.cmd.part-summary.v1"}
DAMAGE_CLASSES = ["background", "missing-part", "broken-part", "scratch", "cracked", "dent", "flaking", "paint-chip",
                  "corrosion"]  # the served checkpoint's own numbering
CARDD_CLASSES = ["background", "dent", "scratch", "crack", "glass-shatter", "lamp-broken", "tire-flat"]
SIGNALS = {"part_area_fraction", "blur_score", "mean_luma", "clipped_fraction", "border_touch_fraction"}


def _checkpoint(folder: Path, labels: int, always: int, id2label: dict[int, str] | None = None) -> bytes:
    """A tiny SegFormer whose classifier answers class ``always`` on every pixel; returns its weight bytes."""
    folder.mkdir(parents=True)
    torch.manual_seed(0)
    names = {"id2label": {str(i): name for i, name in id2label.items()},
             "label2id": {name: i for i, name in id2label.items()}} if id2label else {}
    model = SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=labels, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128], num_attention_heads=[1, 2, 4, 8],
        decoder_hidden_size=32, **names))
    with torch.no_grad():
        model.decode_head.classifier.bias[always] = 1000.0
    model.save_pretrained(folder)
    return (folder / "model.safetensors").read_bytes()


def _records_of(folder: Path, config, weights: bytes, **extra) -> None:
    (folder / "manifest.json").write_text(json.dumps({
        "model_id": config.model_id, "version": config.model_version, "taxonomy_version": config.taxonomy_version,
        "weights_sha256": hashlib.sha256(weights).hexdigest(), "status": "candidate"}), encoding="utf-8")
    (folder / "preprocessing.json").write_text(json.dumps({
        "preprocessing_version": "1.0.0", "input_size": 512, "resize_policy": "longest_edge_pad", "color_space": "RGB",
        "pixel_mean": [0.485, 0.456, 0.406], "pixel_std": [0.229, 0.224, 0.225], "pad_value": 0}), encoding="utf-8")
    for name, content in extra.items():
        (folder / name).write_text(json.dumps(content), encoding="utf-8")


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """Both configured versions: every pixel is a front door, and every pixel is a dent."""
    root = tmp_path / "registry"
    parts, damage = load_parts_config(), load_damage_model_config()
    door = {code: i for i, code in ID_TO_PART_CODE.items()}["front-door"]
    _records_of(root / parts.model_version, parts,
                _checkpoint(root / parts.model_version, 22, door, ID_TO_PART_CODE))
    _records_of(root / damage.model_version, damage,
                _checkpoint(root / damage.model_version, 9, DAMAGE_CLASSES.index("dent")),
                **{"label_schema.json": {"id_to_code": {str(i): c for i, c in enumerate(DAMAGE_CLASSES)}}})
    return root


@pytest.fixture
def versions() -> VersionBundle:
    return VersionBundle.for_runtime("real", "real")


# ---------------------------------------------------------------------------
# The switch, the pinned versions and what each role runs
# ---------------------------------------------------------------------------

def test_the_image_branch_is_fixtures_unless_switched(monkeypatch):
    monkeypatch.delenv("CMEV_PARTS_PRODUCER", raising=False)
    monkeypatch.delenv("CMEV_DAMAGE_PRODUCER", raising=False)
    settings = RuntimeSettings.from_env()
    assert (settings.parts_producer, settings.damage_producer) == ("fixture", "fixture")
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.setenv("CMEV_DAMAGE_PRODUCER", "real")
    settings = RuntimeSettings.from_env()
    assert (settings.parts_producer, settings.damage_producer) == ("real", "real")


def test_real_damage_without_real_parts_is_refused(monkeypatch):
    """The damage worker reads the M1 mask of the photograph; a fixture parts stage has none."""
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "fixture")
    monkeypatch.setenv("CMEV_DAMAGE_PRODUCER", "real")
    with pytest.raises(RuntimeError, match="CMEV_PARTS_PRODUCER"):
        RuntimeSettings.from_env()
    monkeypatch.setenv("CMEV_DAMAGE_PRODUCER", "sometimes")
    with pytest.raises(RuntimeError, match="CMEV_DAMAGE_PRODUCER"):
        RuntimeSettings.from_env()


def test_version_bundle_pins_both_models_and_the_real_summary_rules(versions):
    parts, damage = load_parts_config(), load_damage_model_config()
    fixture = VersionBundle.fixture()
    assert versions.for_stage("parts") == parts.stage_versions(CODE_VERSION)
    assert versions.for_stage("damage") == {
        "damage_model": damage.model_version, "damage_config": damage.config_version,
        "parts_model": parts.model_version, "assignment_config": load_assignment_config().config_version,
        "taxonomy": "damage-hitl-1.0.0", "code": CODE_VERSION}
    assert versions.for_stage("summary") == {"summary_config": load_summary_config().config_version,
                                             "taxonomy": parts.taxonomy_version, "code": CODE_VERSION}
    for stage in ("page_read", "line_items", "pen_marks"):
        assert versions.for_stage(stage) == fixture.for_stage(stage)
    # With only M1 switched, M2 and M3 stay on the fixture tags.
    only_parts = VersionBundle.for_runtime("real")
    assert only_parts.for_stage("damage") == fixture.for_stage("damage")
    assert only_parts.for_stage("summary") == fixture.for_stage("summary")
    with pytest.raises(ValueError, match="parts"):
        VersionBundle.for_runtime("fixture", "real")


def test_a_real_image_branch_leaves_only_the_document_stages_to_the_fixture_producers(database):
    def fixture_groups(**options):
        return {rt.group for rt in build_runtimes(database.session, "producers", versions=VersionBundle.fixture(),
                                                  consolidator=None, profile="full", **options)}

    assert fixture_groups() == IMAGE_GROUPS | DOCUMENT_GROUPS
    assert fixture_groups(parts_producer="real", damage_producer="real") == DOCUMENT_GROUPS


def test_the_image_role_runs_the_three_stage_handlers_and_labels_their_output_real(database):
    handlers = {name: (lambda _ctx: {}) for name in ("parts_handler", "damage_handler", "summary_handler")}
    runtimes = build_runtimes(database.session, "image", profile="lean", source_kind="fixture", **handlers)
    assert {rt.group: rt.topics for rt in runtimes} == {group: (topic,) for group, topic in TOPICS.items()}
    assert {rt.source_kind for rt in runtimes} == {"real"}
    for role, name in (("damage", "damage_handler"), ("summary", "summary_handler")):
        [runtime] = build_runtimes(database.session, role, profile="full", **{name: handlers[name]})
        assert runtime.group == f"cmev-worker-{role}" and runtime.source_kind == "real"
        with pytest.raises(ValueError, match=name):
            build_runtimes(database.session, role, profile="full")
    with pytest.raises(ValueError, match="damage_handler"):
        build_runtimes(database.session, "image", profile="lean", parts_handler=handlers["parts_handler"])


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["damage", "summary", "image"])
def test_a_real_image_worker_refuses_to_start_while_fixtures_own_its_stage(database, monkeypatch, role):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.delenv("CMEV_DAMAGE_PRODUCER", raising=False)
    with pytest.raises(RuntimeError, match="CMEV_DAMAGE_PRODUCER"):
        worker.startup_checks(database, RuntimeSettings.from_env(), role, None)


class _Stopped(BaseException):
    """Ends ``worker.run``'s endless loop without being caught by the worker's own handlers."""


def _isolate_worker(monkeypatch, database, tmp_path: Path) -> dict:
    seen: dict = {"consumers": []}

    def stop(*_args, **_kwargs):
        raise _Stopped

    def consumer(_servers, group, topics, _client):
        seen["consumers"].append((group, tuple(topics)))
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setenv("CMEV_STORAGE_PATH", str(tmp_path / "evidence"))
    monkeypatch.setattr(worker, "Database", lambda: database)
    monkeypatch.setattr(worker, "READY_MARKER", tmp_path / "worker.ready")
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(kafka, "ensure_topics", lambda *_: [])
    monkeypatch.setattr(kafka, "KafkaProducerTransport", lambda *_: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(kafka, "KafkaConsumerTransport", consumer)
    monkeypatch.setattr(worker, "relay_once", stop)
    monkeypatch.setattr(worker.time, "sleep", stop)
    return seen


def test_the_image_worker_loads_both_checkpoints_and_consumes_the_three_image_commands(
        database, registry, tmp_path, monkeypatch):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.setenv("CMEV_DAMAGE_PRODUCER", "real")
    monkeypatch.setenv("CMEV_MODEL_REGISTRY_PATH", str(registry))
    monkeypatch.setenv("CMEV_M8_CONFIG", str(tmp_path / "not-in-this-image.yaml"))  # the worker must not need it
    seen = _isolate_worker(monkeypatch, database, tmp_path)

    with pytest.raises(_Stopped):
        worker.run("image")

    assert dict(seen["consumers"]) == {group: (topic,) for group, topic in TOPICS.items()}
    ready = (tmp_path / "worker.ready").read_text()
    assert load_parts_config().model_version in ready and load_damage_model_config().model_version in ready


def test_the_image_worker_exits_when_the_damage_checkpoint_cannot_be_verified(database, registry, tmp_path, monkeypatch):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.setenv("CMEV_DAMAGE_PRODUCER", "real")
    monkeypatch.setenv("CMEV_MODEL_REGISTRY_PATH", str(registry))
    (registry / load_damage_model_config().model_version / "preprocessing.json").unlink()
    seen = _isolate_worker(monkeypatch, database, tmp_path)

    with pytest.raises(SystemExit) as stopped:
        worker.run("image")

    assert stopped.value.code == 1 and seen["consumers"] == [] and not (tmp_path / "worker.ready").exists()


# ---------------------------------------------------------------------------
# One claim through the three real workers
# ---------------------------------------------------------------------------

def _photo(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 255, (240, 320, 3), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _claim_with_stored_photos(database, storage: Storage, cost_tables, versions: VersionBundle) -> tuple[str, list[str]]:
    """The scenario's claim, with real bytes stored for each photograph (pages stay fixture placeholders)."""
    cid, photo_ids = uid(), []
    with database.session.begin() as db:
        claim = {"claim_id": cid, "reference": f"IT-{cid[-6:]}", "owner_id": "demo-surveyor",
                 "vehicle": {"make": "Test", "model": "Car", "year": 2020,
                             "vehicle_class": _bundle(SCENARIO, cid, 1).vehicle_class},
                 "currency": "SGD", "input_revision": 0, "review_revision": 0, "source_kind": "fixture",
                 "fixture_scenario": SCENARIO, "status": "awaiting_upload"}
        put(db, "claim:" + cid, "claim", claim, cid)
        files = []
        for index, item in enumerate(seed_files(db, claim)):
            if item["role"] == "photograph":
                data, key = _photo(index), f"originals/{item['file_id']}.png"
                storage.write(key, data, "image/png")
                item = {**item, "object_uri": storage.uri(key), "sha256": hashlib.sha256(data).hexdigest(),
                        "byte_count": len(data), "media_type": "image/png", "width": 320, "height": 240,
                        "exif_orientation": 1, "fixture_placeholder": False}
                put(db, "file:" + item["file_id"], "file", item, cid)
                photo_ids.append(item["file_id"])
            files.append(item)
        commit_input_revision(db, claim, files, now=Clock()(), versions=versions,
                              cost_table_version=table_version(cost_tables), profile="lean", source_kind="fixture")
    return cid, photo_ids


def _pipeline(database, storage, registry, cost_tables, versions: VersionBundle) -> LocalPipeline:
    handlers = {
        "parts_handler": make_parts_handler(load_parts_segmenter(load_parts_config(), registry), storage,
                                            versions.for_stage("parts")),
        "damage_handler": make_damage_handler(load_damage_segmenter(load_damage_model_config(), registry), storage,
                                              versions.for_stage("damage")),
        "summary_handler": make_summary_handler(storage, versions.for_stage("summary"))}
    common = {"profile": "lean", "clock": Clock(), "sleep": lambda _s: None}
    runtimes = [*build_runtimes(database.session, "combined", versions=versions,
                                consolidator=Consolidator(cost_table_root=cost_tables), parts_producer="real",
                                damage_producer="real", **common),
                *build_runtimes(database.session, "image", **handlers, **common)]
    return LocalPipeline(database.session, runtimes, clock=Clock())


def _records(db, cid: str, kind: str, revision: int | None = None) -> list[dict]:
    query = select(stage_records.c.body).where(stage_records.c.claim_id == cid, stage_records.c.record_kind == kind)
    if revision is not None:
        query = query.where(stage_records.c.input_revision == revision)
    return list(db.execute(query.order_by(stage_records.c.id)).scalars())


def _stage_jobs(db, cid: str, stage: str, revision: int = 1) -> list[dict]:
    return list(db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == stage,
                                              jobs.c.input_revision == revision)).mappings())


@pytest.fixture
def processed(database, registry, cost_tables, versions, tmp_path):
    """One claim run through the pipeline once: (claim id, photo ids, storage, pipeline)."""
    storage = Storage(directory=tmp_path / "evidence")
    pipeline = _pipeline(database, storage, registry, cost_tables, versions)
    cid, photo_ids = _claim_with_stored_photos(database, storage, cost_tables, versions)
    pipeline.drain()
    return SimpleNamespace(cid=cid, photo_ids=photo_ids, storage=storage, pipeline=pipeline)


def test_every_image_stage_succeeds_on_its_real_worker_and_is_labelled_real(database, processed, versions):
    with database.session() as db:
        for stage in ("parts", "damage", "summary"):
            stage_jobs = _stage_jobs(db, processed.cid, stage)
            assert stage_jobs and all(j["state"] == "succeeded" for j in stage_jobs), stage
            assert {json.dumps(dict(j["versions"]), sort_keys=True) for j in stage_jobs} == \
                {json.dumps(versions.for_stage(stage), sort_keys=True)}
        for kind, service in (("part_prediction", "cmev-worker-parts"), ("damage_observation", "cmev-worker-damage"),
                              ("part_summary", "cmev-worker-summary"), ("part_coverage", "cmev-worker-summary")):
            rows = _records(db, processed.cid, kind)
            assert rows, kind
            assert {(r["provenance"]["source_kind"], r["provenance"]["producer_service"]) for r in rows} == \
                {("real", service)}, kind


def test_damage_is_assigned_against_the_mask_the_parts_worker_made_for_that_photograph(
        database, processed, versions, tmp_path):
    with database.session() as db:
        parts_jobs = {j["target"]: j for j in _stage_jobs(db, processed.cid, "parts")}
        damage_jobs = {j["target"]: j for j in _stage_jobs(db, processed.cid, "damage")}
        observations = _records(db, processed.cid, "damage_observation")
    assert set(damage_jobs) == set(parts_jobs) == set(processed.photo_ids)
    for photo_id, job in damage_jobs.items():
        command = job["command"]["payload"]
        assert command["part_mask_ref"] == parts_jobs[photo_id]["result_ref"]["part_mask_ref"]
        assert command["accepted_parts"] == ["front-door"] and command["taxonomy_version"] == "damage-hitl-1.0.0"
    assert len(observations) == len(processed.photo_ids)  # the checkpoint calls each whole photograph one dent
    for observation in observations:
        photo_id = observation["photo_id"]
        assert (observation["damage_code"], observation["part_code"], observation["side"]) == \
            ("dent", "front-door", "unknown")
        assert observation["versions"] == versions.for_stage("damage")
        assert observation["part_mask_ref"]["artifact_id"] == \
            parts_jobs[photo_id]["result_ref"]["part_mask_ref"]["artifact_id"]
        folder = (tmp_path / "evidence" / "claims" / processed.cid / "1" / "damage" / photo_id /
                  version_signature(versions.for_stage("damage")))
        assert (folder / "mask.png").is_file() and (folder / "components.png").is_file()


def test_the_summary_groups_the_real_observations_and_measures_coverage_on_the_real_masks(database, processed):
    with database.session() as db:
        observations = _records(db, processed.cid, "damage_observation")
        summaries = _records(db, processed.cid, "part_summary")
        coverage = _records(db, processed.cid, "part_coverage")
        confirmations = (_records(db, processed.cid, "identity_confirmation")
                         + _records(db, processed.cid, "coverage_confirmation"))
    [group] = summaries
    assert (group["identity_status"], group["part_code"], group["side"]) == ("part_only", "front-door", "unknown")
    assert sorted(group["member_observation_ids"]) == sorted(o["observation_id"] for o in observations)
    assert group["damage_codes"] == ["dent"] and sorted(group["supporting_photo_ids"]) == sorted(processed.photo_ids)
    # No surveyor has said which door this is or that the views show enough of it: nothing is judged.
    assert confirmations == []
    assert {c["state"] for c in coverage} == {"unresolved"}
    door = next(c for c in coverage if c["part_code"] == "front-door")
    assert door["reasons"] == ["identity_not_resolved"] and door["side"] == "unknown"
    assert sorted(v["photo_id"] for v in door["views"]) == sorted(processed.photo_ids)
    for view in door["views"]:  # measured on the photograph inside the M1 mask, not copied from a fixture
        assert set(view["signals"]) == SIGNALS
        assert view["signals"]["part_area_fraction"] == pytest.approx(384 / 512)  # a 4:3 photograph in a square frame
        assert 100 < view["signals"]["mean_luma"] < 155  # uniform noise


def test_the_assessment_is_made_from_the_real_image_records_and_passes_no_photo_check(database, processed):
    with database.session() as db:
        assessment = db.execute(select(assessments.c.body).where(assessments.c.claim_id == processed.cid)).scalar_one()
        summary_ids = {s["summary_id"] for s in _records(db, processed.cid, "part_summary")}
        consolidate = _stage_jobs(db, processed.cid, "consolidate")[0]
    assert set(consolidate["command"]["payload"]["summary_ids"]) == summary_ids
    assert consolidate["command"]["payload"]["image_branch_state"] == "complete"
    photo_results = {f["photographic_check"]["result"] for f in assessment["findings"]
                     if f.get("photographic_check")}
    assert "passed" not in photo_results and "failed" not in photo_results
    # The document stages and the cost table are still fixtures, so the assessment still says so.
    assert assessment["provenance"]["source_kind"] == "fixture"


def _confirm(database, cid: str, cost_tables, versions, events: list[dict]) -> int:
    """Commit the next input revision carrying surveyor confirmations, reusing every stage but the summary."""
    with database.session.begin() as db:
        claim = dict(get(db, "claim:" + cid))
        previous = get(db, f"input:{cid}:{claim['input_revision']}")
        files = [get(db, "file:" + fid) for fid in previous["file_ids"]]
        corrections = []
        for index, values in enumerate(events, start=1):
            action = values.pop("action_type")
            corrections.append({"kind": "review_event", "event": {
                "event_id": deterministic_id("re", cid, index), "action_id": deterministic_id("ra", cid, index),
                "claim_id": cid, "assessment_revision": 1, "expected_review_revision": index - 1,
                "resulting_review_revision": index, "actor": "surveyor:test", "recorded_at": "2026-09-24T04:00:00Z",
                "action_type": action, "new_values": values, "idempotency_key": f"confirm-{cid}-{index}"}})
        result = commit_input_revision(
            db, claim, files, now=Clock()(), versions=versions, cost_table_version=table_version(cost_tables),
            profile="lean", source_kind="fixture", corrections=corrections, reuse_from=claim["input_revision"],
            reuse_stages=[s for s in STAGES if s != "summary"], review_revision=len(events))
    return result["input_revision"]


def test_a_surveyors_confirmation_reruns_only_the_summary_over_the_stored_model_output(
        database, processed, cost_tables, versions):
    with database.session() as db:
        before = {kind: _records(db, processed.cid, kind, 1) for kind in ("part_summary", "part_coverage")}

    revision = _confirm(database, processed.cid, cost_tables, versions, [
        {"action_type": "confirm_identity", "part_code": "front-door", "side": "left",
         "photo_ids": list(processed.photo_ids)},
        {"action_type": "confirm_coverage", "part_code": "front-door", "side": "left",
         "photo_ids": list(processed.photo_ids), "covers_enough": True}])
    processed.pipeline.drain()

    with database.session() as db:
        assert _stage_jobs(db, processed.cid, "parts", revision) == []   # no model ran again
        assert _stage_jobs(db, processed.cid, "damage", revision) == []
        [summary_job] = _stage_jobs(db, processed.cid, "summary", revision)
        [group] = _records(db, processed.cid, "part_summary", revision)
        coverage = _records(db, processed.cid, "part_coverage", revision)
        identities = _records(db, processed.cid, "identity_confirmation", revision)
        unchanged = {kind: _records(db, processed.cid, kind, 1) for kind in before}
    assert summary_job["state"] == "succeeded" and summary_job["result_ref"]["reuse_from_input_revision"] == 1
    assert (group["identity_status"], group["part_code"], group["side"]) == ("resolved", "front-door", "left")
    assert sorted(group["identity_confirmation_ids"]) == sorted(c["confirmation_id"] for c in identities)
    assert {c["provenance"]["source_kind"] for c in identities} == {"real"}
    left = next(c for c in coverage if (c["part_code"], c["side"]) == ("front-door", "left"))
    # The mask fills the photograph, so every view is cut off at its border: the surveyor's word does
    # not override the screen, and the slot is inadequate with the measured reason.
    assert left["state"] == "inadequate" and "cropped_at_border" in left["reasons"]
    assert all(set(view["signals"]) == SIGNALS for view in left["views"])
    assert unchanged == before  # revision 1 is history and is not rewritten


def test_a_cardd_model_is_served_by_changing_only_the_damage_configuration(
        database, cost_tables, tmp_path, monkeypatch):
    """The vocabulary of the whole branch follows the damage configuration; nothing else names it."""
    served = tmp_path / "damage.yaml"
    served.write_text(yaml.safe_dump({
        "model_id": "damage-cardd", "model_version": "damage-cardd/0.1.0-test",
        "taxonomy_version": "damage-cardd-1.0.0", "config_version": "damage-cfg-0.1.0", "input_size": 512}),
        encoding="utf-8")
    monkeypatch.setenv("CMEV_DAMAGE_CONFIG", str(served))
    root, parts, damage = tmp_path / "registry", load_parts_config(), load_damage_model_config()
    door = {code: i for i, code in ID_TO_PART_CODE.items()}["front-door"]
    _records_of(root / parts.model_version, parts, _checkpoint(root / parts.model_version, 22, door, ID_TO_PART_CODE))
    # An entry as the notebook export writes it: the class names are in the checkpoint, in CarDD order.
    _records_of(root / damage.model_version, damage, _checkpoint(
        root / damage.model_version, 7, CARDD_CLASSES.index("glass-shatter"), dict(enumerate(CARDD_CLASSES))))
    versions = VersionBundle.for_runtime("real", "real")
    storage = Storage(directory=tmp_path / "evidence")
    pipeline = _pipeline(database, storage, root, cost_tables, versions)
    cid, photo_ids = _claim_with_stored_photos(database, storage, cost_tables, versions)
    pipeline.drain()

    with database.session() as db:
        damage_jobs = _stage_jobs(db, cid, "damage")
        observations = _records(db, cid, "damage_observation")
        [group] = _records(db, cid, "part_summary")
        [assessment] = db.execute(select(assessments.c.body).where(assessments.c.claim_id == cid)).scalars()
    assert len(damage_jobs) == len(photo_ids) and {j["state"] for j in damage_jobs} == {"succeeded"}
    assert {j["command"]["payload"]["taxonomy_version"] for j in damage_jobs} == {"damage-cardd-1.0.0"}
    # glass-shatter exists only in the CarDD vocabulary.
    assert len(observations) == len(photo_ids)
    assert {(o["damage_code"], o["versions"]["taxonomy"], o["versions"]["damage_model"]) for o in observations} == \
        {("glass-shatter", "damage-cardd-1.0.0", "damage-cardd/0.1.0-test")}
    assert group["damage_codes"] == ["glass-shatter"] and group["part_code"] == "front-door"
    assert assessment["findings"]


def test_a_damage_command_pinned_to_the_fixture_versions_is_dead_lettered(database, registry, cost_tables, tmp_path):
    """An orchestrator that still pins the fixture tags must not get real rows recorded under them."""
    storage = Storage(directory=tmp_path / "evidence")
    real = VersionBundle.for_runtime("real", "real")
    stale = VersionBundle(stages={**real.stages, "damage": VersionBundle.fixture().for_stage("damage")},
                          intake=real.intake)
    handlers = {
        "parts_handler": make_parts_handler(load_parts_segmenter(load_parts_config(), registry), storage,
                                            real.for_stage("parts")),
        "damage_handler": make_damage_handler(load_damage_segmenter(load_damage_model_config(), registry), storage,
                                              real.for_stage("damage")),
        "summary_handler": make_summary_handler(storage, real.for_stage("summary"))}
    common = {"profile": "lean", "clock": Clock(), "sleep": lambda _s: None}
    pipeline = LocalPipeline(database.session, [
        *build_runtimes(database.session, "combined", versions=stale,
                        consolidator=Consolidator(cost_table_root=cost_tables), parts_producer="real",
                        damage_producer="real", **common),
        *build_runtimes(database.session, "image", **handlers, **common)], clock=Clock())
    cid, _ = _claim_with_stored_photos(database, storage, cost_tables, stale)

    pipeline.drain()

    with database.session() as db:
        damage_jobs = _stage_jobs(db, cid, "damage")
        observations = _records(db, cid, "damage_observation")
    assert damage_jobs and {(j["state"], j["reason_code"]) for j in damage_jobs} == \
        {("dead_lettered", "model_version_unsupported")}
    assert observations == []
