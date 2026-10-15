"""Pinned OCR release integrity and lightweight orchestrator version selection."""
import json
from pathlib import Path
import pytest
from claim_cmev.documents.text_layout.model_config import load_ocr_model_config, verify_ocr_registry

REPO = Path(__file__).resolve().parents[3]

def test_stage_versions_match_adapter_engine_conventions():
    cfg = load_ocr_model_config()
    versions = cfg.stage_versions('test-code')
    assert versions['ocr_engine'] == 'paddleocr'
    assert versions['ocr_version'] == '2.10.0'
    assert versions['ocr'] == 'paddleocr/2.10.0'
    assert versions['ocr_weights'] == cfg.model_version
    assert versions['preprocess_config'] == versions['page_config']
    assert len(versions['ocr_manifest_sha256']) == 64
    assert len(versions['page_config_sha256']) == 64


def test_registry_rejects_unpinned_manifest(tmp_path):
    cfg = load_ocr_model_config()
    root = cfg.model_dir(tmp_path); root.mkdir(parents=True)
    (root / 'manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='differs from pinned'):
        verify_ocr_registry(cfg, tmp_path)


def test_registry_rejects_missing_or_corrupt_artifact(tmp_path):
    cfg = load_ocr_model_config()
    root = cfg.model_dir(tmp_path); root.mkdir(parents=True)
    (root / 'manifest.json').write_text((REPO / 'configs/models/ocr-manifest.json').read_text())
    with pytest.raises(ValueError, match='missing or corrupt'):
        verify_ocr_registry(cfg, tmp_path)


def test_registry_config_runtime_must_match_manifest(tmp_path):
    cfg = load_ocr_model_config().model_copy(update={'runtime_version':'3.0.0'})
    root = cfg.model_dir(tmp_path); root.mkdir(parents=True)
    (root / 'manifest.json').write_text((REPO / 'configs/models/ocr-manifest.json').read_text())
    with pytest.raises(ValueError, match='configuration differs'):
        verify_ocr_registry(cfg, tmp_path)


def test_model_version_cannot_escape_registry(tmp_path):
    config = json.loads(json.dumps(load_ocr_model_config().model_dump()))
    config['model_version'] = '../outside'
    path = tmp_path / 'ocr.yaml'; path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='relative registry'):
        load_ocr_model_config(path)
