"""M1 serving prerequisites: configuration, registry lookup, checkpoint verification, pinned versions.

Specification: docs/specs/module-01-vehicle-part-segmentation.md ("A missing or invalid
configured checkpoint fails container readiness") and docs/specs/technical_specification.md
section 9.3 (read-only registry, manifest and hash verification at worker start).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import numpy as np
from PIL import Image
import pytest
import torch
from transformers import SegformerConfig, SegformerForSemanticSegmentation

from claim_cmev.contracts.common import version_signature
from claim_cmev.contracts.events import Envelope
from claim_cmev.contracts.fixtures import VERSIONS as FIXTURE_VERSIONS
from claim_cmev.messaging.consumer import Context, PermanentError
from claim_cmev.runtime import utcnow
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts import adapter as parts_adapter
from claim_cmev.vision.parts.config import PartsConfig, load_parts_config

CLAIM_ID = "01JAX7Q0VN4Z3K9F2M8R6T1C5D"
CODE_VERSION = "0.2.0"


def write_registry_entry(registry: Path, config: PartsConfig, id2label: dict[int, str] | None = None) -> Path:
    """A tiny random-weight SegFormer with the manifest the trainer writes, under ``registry/<model_version>``."""
    labels = id2label or ID_TO_PART_CODE
    model_dir = registry / config.model_version
    model_dir.mkdir(parents=True)
    torch.manual_seed(0)
    SegformerForSemanticSegmentation(SegformerConfig(
        num_labels=len(labels), id2label={str(i): labels[i] for i in sorted(labels)},
        label2id={labels[i]: i for i in sorted(labels)}, depths=[1, 1, 1, 1], hidden_sizes=[16, 32, 64, 128],
        num_attention_heads=[1, 2, 4, 8], decoder_hidden_size=32)).save_pretrained(model_dir)
    weights = (model_dir / "model.safetensors").read_bytes()
    (model_dir / "manifest.json").write_text(json.dumps({
        "model_id": config.model_id, "version": config.model_version, "taxonomy_version": config.taxonomy_version,
        "weights_sha256": hashlib.sha256(weights).hexdigest(), "status": "candidate"}), encoding="utf-8")
    return model_dir


@pytest.fixture
def config() -> PartsConfig:
    return PartsConfig(model_version="parts/9.9.9-test", device="cpu", write_overlay=False)


@pytest.fixture
def registry(tmp_path: Path, config: PartsConfig) -> Path:
    root = tmp_path / "registry"
    write_registry_entry(root, config)
    return root


# ---------------------------------------------------------------------------
# Configuration and registry lookup
# ---------------------------------------------------------------------------

def test_model_dir_is_the_registry_folder_named_by_the_model_version(tmp_path: Path):
    config = PartsConfig(model_version="parts/0.5.0-b2")
    assert config.model_dir(tmp_path) == tmp_path / "parts" / "0.5.0-b2"


def test_registry_root_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CMEV_MODEL_REGISTRY_PATH", str(tmp_path / "mounted"))
    assert PartsConfig(model_version="parts/0.1.0").model_dir() == tmp_path / "mounted" / "parts" / "0.1.0"


def test_stage_versions_pin_the_model_its_thresholds_and_the_taxonomy():
    config = PartsConfig(model_version="parts/0.5.0-b2", config_version="parts-cfg-0.5.0", taxonomy_version="parts-1.0.0")
    assert config.stage_versions(CODE_VERSION) == {
        "parts_model": "parts/0.5.0-b2", "parts_config": "parts-cfg-0.5.0", "taxonomy": "parts-1.0.0",
        "code": CODE_VERSION}


def test_load_parts_config_reads_the_path_named_by_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "parts.yaml"
    path.write_text('model_version: "parts/7.0.0"\n', encoding="utf-8")
    monkeypatch.setenv("CMEV_PARTS_CONFIG", str(path))
    assert load_parts_config().model_version == "parts/7.0.0"


def test_load_parts_config_refuses_a_missing_file(tmp_path: Path):
    """Built-in defaults must never stand in for a versioned configuration that is not there."""
    with pytest.raises(FileNotFoundError):
        load_parts_config(tmp_path / "absent.yaml")


def test_reading_the_parts_config_does_not_load_the_model_stack():
    """The orchestrator and API read this configuration; they must not import PyTorch to do it."""
    code = "import sys, claim_cmev.vision.parts.config; sys.exit(int('torch' in sys.modules))"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    assert subprocess.run([sys.executable, "-c", code], env=env, check=False).returncode == 0


# ---------------------------------------------------------------------------
# Loading and verifying the checkpoint
# ---------------------------------------------------------------------------

def test_load_parts_segmenter_serves_the_verified_registry_entry(registry: Path, config: PartsConfig):
    segmenter = parts_adapter.load_parts_segmenter(config, registry)
    assert segmenter.model_dir == registry / "parts" / "9.9.9-test"
    assert segmenter.id_to_part_code == ID_TO_PART_CODE


def _remove_folder(model_dir: Path) -> None:
    for path in sorted(model_dir.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    model_dir.rmdir()


def _edit_manifest(**changes):
    def apply(model_dir: Path) -> None:
        path = model_dir / "manifest.json"
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **changes}), encoding="utf-8")
    return apply


def _append_to_weights(model_dir: Path) -> None:
    with (model_dir / "model.safetensors").open("ab") as handle:
        handle.write(b"tampered")


@pytest.mark.parametrize(("damage", "reason_code"), [
    pytest.param(_remove_folder, "model_not_found", id="no-registry-entry"),
    pytest.param(lambda d: (d / "manifest.json").unlink(), "model_manifest_missing", id="no-manifest"),
    pytest.param(_edit_manifest(version="parts/0.0.1"), "model_version_mismatch", id="another-version"),
    pytest.param(_edit_manifest(taxonomy_version="parts-2.0.0"), "taxonomy_version_mismatch", id="another-taxonomy"),
    pytest.param(lambda d: (d / "model.safetensors").unlink(), "model_weights_missing", id="no-weights"),
    pytest.param(_append_to_weights, "model_weights_hash_mismatch", id="changed-weights"),
])
def test_load_parts_segmenter_refuses_an_entry_it_cannot_verify(registry: Path, config: PartsConfig, damage, reason_code):
    damage(registry / "parts" / "9.9.9-test")
    with pytest.raises(parts_adapter.ModelUnavailable) as refused:
        parts_adapter.load_parts_segmenter(config, registry)
    assert refused.value.reason_code == reason_code


def test_load_parts_segmenter_refuses_a_checkpoint_with_another_label_map(tmp_path: Path, config: PartsConfig):
    """A worker may not renumber classes: class 1 and 2 swapped is a refusal, not a silent remap."""
    swapped = {**ID_TO_PART_CODE, 1: ID_TO_PART_CODE[2], 2: ID_TO_PART_CODE[1]}
    write_registry_entry(tmp_path / "registry", config, id2label=swapped)
    with pytest.raises(parts_adapter.ModelUnavailable) as refused:
        parts_adapter.load_parts_segmenter(config, tmp_path / "registry")
    assert refused.value.reason_code == "label_map_mismatch"


# ---------------------------------------------------------------------------
# The handler serves only commands pinned to the versions it loaded
# ---------------------------------------------------------------------------

def _photo() -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((48, 64, 3), 128, dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _command(storage: Storage, versions: dict[str, str]) -> Context:
    photo = _photo()
    storage.write("originals/ph_01.png", photo, "image/png")
    envelope = Envelope.build(
        topic="cmev.cmd.parts-segment.v1", claim_id=CLAIM_ID, input_revision=1, task="parts_segment",
        versions=versions, trace_id="trace-12345", occurred_at=utcnow(), target="ph_01",
        provenance={"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "cmev-orchestrator"})
    payload = {"photo": {"file_id": "ph_01", "object_uri": storage.uri("originals/ph_01.png"),
                         "sha256": hashlib.sha256(photo).hexdigest(), "media_type": "image/png",
                         "byte_count": len(photo), "width": 64, "height": 48, "page_number": None,
                         "exif_orientation": 1},
               "model_id": "parts", "model_version": "9.9.9-test", "preprocess_config_version": "parts-cfg-0.1.0",
               "taxonomy_version": "parts-1.0.0"}
    context = Context(session=MagicMock(), topic="cmev.cmd.parts-segment.v1", message=envelope.message(payload),
                      envelope=envelope, group="cmev-worker-parts", service="cmev-worker-parts", profile="lean",
                      source_kind="real", now=utcnow(),
                      job={"job_key": envelope.job_key, "task": "parts_segment", "target": "ph_01", "attempt_epoch": 0})
    context.emit = lambda topic, message, **kwargs: None
    return context


def test_handler_refuses_a_command_pinned_to_other_versions(registry: Path, config: PartsConfig, tmp_path: Path):
    """A command the orchestrator pinned to the fixture versions must not get real rows under that label."""
    storage = Storage(directory=tmp_path / "evidence")
    handler = parts_adapter.make_parts_handler(parts_adapter.load_parts_segmenter(config, registry), storage, config.stage_versions(CODE_VERSION))
    context = _command(storage, dict(FIXTURE_VERSIONS["parts"]))

    with pytest.raises(PermanentError) as refused:
        handler(context)

    assert refused.value.reason_code == "model_version_unsupported"
    assert not context.session.execute.called
    assert not (tmp_path / "evidence" / "claims").exists()


def test_handler_serves_a_command_pinned_to_the_loaded_versions(registry: Path, config: PartsConfig, tmp_path: Path):
    storage = Storage(directory=tmp_path / "evidence")
    versions = config.stage_versions(CODE_VERSION)
    handler = parts_adapter.make_parts_handler(parts_adapter.load_parts_segmenter(config, registry), storage, versions)

    result = handler(_command(storage, versions))

    assert result["photo_id"] == "ph_01"
    folder = tmp_path / "evidence" / "claims" / CLAIM_ID / "1" / "parts" / "ph_01" / version_signature(versions)
    assert (folder / "mask.png").is_file()


def test_load_parts_segmenter_refuses_weights_that_do_not_all_load(tmp_path: Path, config: PartsConfig):
    """Another library version may name a layer differently. The checkpoint then "loads" with that
    layer left at random values, which is a model that answers confidently and wrongly."""
    from safetensors.torch import load_file, save_file

    model_dir = write_registry_entry(tmp_path / "registry", config)
    weights = model_dir / "model.safetensors"
    state = load_file(weights)
    state["decode_head.renamed_by_another_version.weight"] = state.pop("decode_head.classifier.weight")
    save_file(state, weights, metadata={"format": "pt"})
    manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["weights_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()  # the file is intact; its names are not
    (model_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(parts_adapter.ModelUnavailable) as refused:
        parts_adapter.load_parts_segmenter(config, tmp_path / "registry")

    assert refused.value.reason_code == "model_weights_incomplete"
    assert "decode_head.classifier.weight" in str(refused.value)
