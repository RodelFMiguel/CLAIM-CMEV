"""The real M1 worker beside the fixture pipeline: the opt-in switch, start-up and honest labelling.

SQLite and the in-memory transport, as in the other runtime integration tests. The
checkpoint is a tiny random-weight SegFormer, so these check wiring, versions and
provenance. They say nothing about model accuracy, Kafka or Docker.
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

from claim_cmev import worker
from claim_cmev.contracts.common import version_signature
from claim_cmev.contracts.fixtures import VERSIONS as FIXTURE_VERSIONS
from claim_cmev.fixtures import _bundle, seed_files
from claim_cmev.messaging import kafka
from claim_cmev.orchestration.consolidation import Consolidator
from claim_cmev.orchestration.intake import commit_input_revision
from claim_cmev.orchestration.plan import CODE_VERSION, VersionBundle
from claim_cmev.orchestration.services import LocalPipeline, RuntimeSettings, build_runtimes
from claim_cmev.persistence.tables import assessments, jobs, stage_records
from claim_cmev.runtime import put, uid
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts.adapter import load_parts_segmenter, make_parts_handler
from claim_cmev.vision.parts.config import load_parts_config
from pipeline_helpers import Clock, table_version

PARTS_GROUP = "cmev-worker-parts"
PARTS_TOPIC = "cmev.cmd.parts-segment.v1"
OTHER_FIXTURE_GROUPS = {"cmev-worker-damage", "cmev-worker-summary", "cmev-worker-ocr", "cmev-worker-lineitems",
                        "cmev-worker-penmarks"}
SCENARIO = "exclusion_and_supported"


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """A registry holding the configured parts version: a tiny random-weight SegFormer and its manifest."""
    config = load_parts_config()
    model_dir = tmp_path / "registry" / config.model_version
    model_dir.mkdir(parents=True)
    torch.manual_seed(0)
    SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=22, id2label={str(i): ID_TO_PART_CODE[i] for i in range(22)},
        label2id={ID_TO_PART_CODE[i]: i for i in range(22)}, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128],
        num_attention_heads=[1, 2, 4, 8], decoder_hidden_size=32)).save_pretrained(model_dir)
    weights = (model_dir / "model.safetensors").read_bytes()
    (model_dir / "manifest.json").write_text(json.dumps({
        "model_id": config.model_id, "version": config.model_version, "taxonomy_version": config.taxonomy_version,
        "weights_sha256": hashlib.sha256(weights).hexdigest(), "status": "candidate"}), encoding="utf-8")
    (model_dir / "preprocessing.json").write_text(json.dumps({
        "preprocessing_version": "1.0.0", "input_size": 512, "resize_policy": "longest_edge_pad", "color_space": "RGB",
        "pixel_mean": [0.485, 0.456, 0.406], "pixel_std": [0.229, 0.224, 0.225], "pad_value": 0}), encoding="utf-8")
    return tmp_path / "registry"


@pytest.fixture
def real_versions() -> dict[str, str]:
    return load_parts_config().stage_versions(CODE_VERSION)


# ---------------------------------------------------------------------------
# The switch and what each role runs
# ---------------------------------------------------------------------------

def test_parts_stay_with_the_fixture_producer_unless_switched(monkeypatch):
    monkeypatch.delenv("CMEV_PARTS_PRODUCER", raising=False)
    assert RuntimeSettings.from_env().parts_producer == "fixture"
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    assert RuntimeSettings.from_env().parts_producer == "real"


def test_an_unknown_parts_producer_is_refused(monkeypatch):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "both")
    with pytest.raises(RuntimeError, match="CMEV_PARTS_PRODUCER"):
        RuntimeSettings.from_env()


def test_version_bundle_pins_the_configured_model_only_in_real_parts_mode(real_versions):
    fixture, real = VersionBundle.fixture(), VersionBundle.for_runtime("real")
    assert VersionBundle.for_runtime("fixture") == fixture
    assert real.for_stage("parts") == real_versions
    assert {stage: real.for_stage(stage) for stage in real.stages if stage != "parts"} == \
        {stage: fixture.for_stage(stage) for stage in fixture.stages if stage != "parts"}


def test_real_parts_mode_removes_only_the_fixture_parts_producer(database):
    def fixture_groups(**options):
        return {rt.group for rt in build_runtimes(database.session, "producers", versions=VersionBundle.fixture(),
                                                  consolidator=None, profile="full", **options)}

    assert fixture_groups() == OTHER_FIXTURE_GROUPS | {PARTS_GROUP}
    assert fixture_groups(parts_producer="real") == OTHER_FIXTURE_GROUPS


def test_parts_role_runs_the_supplied_handler_and_labels_its_output_real(database):
    def handler(_ctx):
        return {}

    # The service environment still says fixture mode; a model worker never labels its rows that way.
    runtimes = build_runtimes(database.session, "parts", profile="full", source_kind="fixture", parts_handler=handler)

    assert [(rt.group, rt.service, rt.topics) for rt in runtimes] == [(PARTS_GROUP, PARTS_GROUP, (PARTS_TOPIC,))]
    assert runtimes[0].handlers[PARTS_TOPIC] is handler
    assert (runtimes[0].source_kind, runtimes[0].profile) == ("real", "full")


def test_parts_role_needs_a_loaded_handler(database):
    with pytest.raises(ValueError, match="parts_handler"):
        build_runtimes(database.session, "parts", profile="lean")


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------

def test_parts_worker_refuses_to_start_while_the_fixture_producer_owns_parts(database, monkeypatch):
    monkeypatch.delenv("CMEV_PARTS_PRODUCER", raising=False)
    with pytest.raises(RuntimeError, match="CMEV_PARTS_PRODUCER"):
        worker.startup_checks(database, RuntimeSettings.from_env(), "parts", None)


def test_parts_worker_startup_needs_no_consolidator(database, monkeypatch):
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    info = worker.startup_checks(database, RuntimeSettings.from_env(), "parts", None)
    assert info["role"] == "parts" and info["schema_head"]


class _Stopped(BaseException):
    """Ends ``worker.run``'s endless loop. Not an ``Exception``, so the worker's own handlers let it through;
    not ``KeyboardInterrupt``, so an unexpected one fails the test instead of aborting the session."""


def _isolate_worker(monkeypatch, database, tmp_path: Path, *, stop_at: str) -> dict:
    """Run ``worker.run`` against the test database with Kafka replaced by recording stubs.

    ``stop_at="relay"`` ends the run at the first relay, once consumers are up.
    ``stop_at="reconnect"`` ends it when the worker gives up an attempt and waits to retry.
    """
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
    monkeypatch.setattr(worker, "relay_once", stop if stop_at == "relay" else lambda *_a, **_k: 0)
    monkeypatch.setattr(worker.time, "sleep", stop)
    return seen


def test_parts_worker_exits_when_its_checkpoint_cannot_be_verified(database, tmp_path, monkeypatch):
    """An absent model is a refusal to start, not a retry loop that looks like a broker outage."""
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.setenv("CMEV_MODEL_REGISTRY_PATH", str(tmp_path / "empty-registry"))
    seen = _isolate_worker(monkeypatch, database, tmp_path, stop_at="reconnect")

    with pytest.raises(SystemExit) as stopped:
        worker.run("parts")

    assert stopped.value.code == 1
    assert seen["consumers"] == [] and not (tmp_path / "worker.ready").exists()


def test_parts_worker_starts_without_the_m8_and_m3_configuration(database, registry, tmp_path, monkeypatch):
    """The parts image ships neither file; the worker must not need what it never uses."""
    monkeypatch.setenv("CMEV_PARTS_PRODUCER", "real")
    monkeypatch.setenv("CMEV_MODEL_REGISTRY_PATH", str(registry))
    monkeypatch.setenv("CMEV_M8_CONFIG", str(tmp_path / "not-in-this-image.yaml"))
    monkeypatch.setenv("CMEV_M3_CONFIG", str(tmp_path / "not-in-this-image.yaml"))
    seen = _isolate_worker(monkeypatch, database, tmp_path, stop_at="relay")

    with pytest.raises(_Stopped):
        worker.run("parts")

    assert seen["consumers"] == [(PARTS_GROUP, (PARTS_TOPIC,))]
    assert load_parts_config().model_version in (tmp_path / "worker.ready").read_text()


# ---------------------------------------------------------------------------
# One claim through the real parts worker and the fixture stages
# ---------------------------------------------------------------------------

def _photo(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 255, (240, 320, 3), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _claim_with_stored_photos(database, storage: Storage, cost_tables, versions: VersionBundle) -> tuple[str, list[str]]:
    """The scenario's claim, with real bytes stored for each photograph (pages stay placeholders)."""
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


def _pipeline_with_real_parts(database, storage, registry, cost_tables, orchestrator_versions: VersionBundle,
                              real_versions) -> LocalPipeline:
    config = load_parts_config()
    handler = make_parts_handler(load_parts_segmenter(config, registry), storage, real_versions)
    common = {"profile": "lean", "clock": Clock(), "sleep": lambda _s: None}
    runtimes = [*build_runtimes(database.session, "combined", versions=orchestrator_versions,
                                consolidator=Consolidator(cost_table_root=cost_tables), parts_producer="real", **common),
                *build_runtimes(database.session, "parts", parts_handler=handler, **common)]
    return LocalPipeline(database.session, runtimes, clock=Clock())


def _records(db, cid: str, kind: str) -> list[dict]:
    return list(db.execute(select(stage_records.c.body).where(
        stage_records.c.claim_id == cid, stage_records.c.record_kind == kind)).scalars())


def test_stored_photos_run_through_the_real_parts_worker_while_other_stages_stay_fixtures(
        database, registry, cost_tables, real_versions, tmp_path):
    storage = Storage(directory=tmp_path / "evidence")
    versions = VersionBundle.for_runtime("real")
    pipeline = _pipeline_with_real_parts(database, storage, registry, cost_tables, versions, real_versions)
    cid, photo_ids = _claim_with_stored_photos(database, storage, cost_tables, versions)

    pipeline.drain()

    with database.session() as db:
        parts_jobs = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == "parts")).mappings().all()
        quality, predictions = _records(db, cid, "image_quality"), _records(db, cid, "part_prediction")
        observations = _records(db, cid, "damage_observation")
        assessment = db.execute(select(assessments.c.body).where(assessments.c.claim_id == cid)).scalar_one()
    assert {j["target"] for j in parts_jobs} == set(photo_ids)
    assert all(j["state"] == "succeeded" and dict(j["versions"]) == real_versions for j in parts_jobs)
    assert {j["command"]["payload"]["preprocess_config_version"] for j in parts_jobs} == {real_versions["parts_config"]}
    # M1 rows come from the checkpoint and say so.
    assert {q["photo_id"] for q in quality} == set(photo_ids)
    assert predictions and {p["photo_id"] for p in predictions} <= set(photo_ids)
    for row in [*quality, *predictions]:
        assert row["provenance"]["source_kind"] == "real"
        assert row["provenance"]["producer_service"] == PARTS_GROUP
        assert row["versions"] == real_versions
    for photo_id in photo_ids:
        folder = tmp_path / "evidence" / "claims" / cid / "1" / "parts" / photo_id / version_signature(real_versions)
        assert (folder / "mask.png").is_file()
    # M2 to M8 are still fixtures, and the assessment is still labelled one.
    assert observations and all(o["provenance"]["source_kind"] == "fixture" for o in observations)
    assert assessment["provenance"]["source_kind"] == "fixture"


def test_real_worker_dead_letters_a_parts_command_pinned_to_the_fixture_versions(
        database, registry, cost_tables, real_versions, tmp_path):
    """A misconfigured orchestrator must not get real rows recorded under the fixture model's name."""
    storage = Storage(directory=tmp_path / "evidence")
    fixture_versions = VersionBundle.fixture()
    pipeline = _pipeline_with_real_parts(database, storage, registry, cost_tables, fixture_versions, real_versions)
    cid, _photo_ids = _claim_with_stored_photos(database, storage, cost_tables, fixture_versions)

    pipeline.drain()

    with database.session() as db:
        parts_jobs = db.execute(select(jobs).where(jobs.c.claim_id == cid, jobs.c.stage == "parts")).mappings().all()
        predictions = _records(db, cid, "part_prediction")
    assert parts_jobs and all(dict(j["versions"]) == FIXTURE_VERSIONS["parts"] for j in parts_jobs)
    assert {(j["state"], j["reason_code"]) for j in parts_jobs} == {("dead_lettered", "model_version_unsupported")}
    assert predictions == []
    assert not (tmp_path / "evidence" / "claims").exists()
