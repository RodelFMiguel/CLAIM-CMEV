"""Independent integrity checks for every native OCR artifact before loading Paddle."""
import hashlib
import json
import subprocess
import sys

import pytest

from claim_cmev.documents.text_layout.model_config import load_ocr_model_config, verify_ocr_registry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    config = load_ocr_model_config()
    root = config.model_dir(tmp_path)
    root.mkdir(parents=True)
    files = {}
    for component in ('det', 'rec', 'cls'):
        for name in ('inference.pdmodel', 'inference.pdiparams', 'inference.pdiparams.info'):
            relative = f'{component}/{name}'
            content = relative.encode()
            target = root / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(content)
            files[relative] = hashlib.sha256(content).hexdigest()
    manifest = {'model_version': config.model_version, 'engine': config.engine,
                'engine_version': config.engine_version, 'runtime_version': config.runtime_version,
                'files': files}
    pinned = tmp_path / 'pinned.json'
    pinned.write_text(json.dumps(manifest))
    (root / 'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setenv('CMEV_OCR_MANIFEST', str(pinned))
    return config, tmp_path, root


@pytest.mark.parametrize('component', ['det', 'rec', 'cls'])
@pytest.mark.parametrize('name', ['inference.pdmodel', 'inference.pdiparams', 'inference.pdiparams.info'])
def test_each_native_artifact_corruption_is_rejected(registry, component, name):
    config, registry_root, root = registry
    assert verify_ocr_registry(config, registry_root) == root
    (root / component / name).write_bytes(b'changed')
    with pytest.raises(ValueError, match='missing or corrupt'):
        verify_ocr_registry(config, registry_root)


def test_manifest_cannot_self_authorize_changed_weights(registry):
    config, registry_root, root = registry
    target = root / 'rec/inference.pdiparams'
    target.write_bytes(b'changed')
    installed = json.loads((root / 'manifest.json').read_text())
    installed['files']['rec/inference.pdiparams'] = hashlib.sha256(b'changed').hexdigest()
    (root / 'manifest.json').write_text(json.dumps(installed))
    with pytest.raises(ValueError, match='differs from pinned'):
        verify_ocr_registry(config, registry_root)


def test_orchestrator_ocr_pins_do_not_load_native_inference():
    code = """
import sys
from claim_cmev.orchestration.plan import VersionBundle
pins = VersionBundle.for_runtime(ocr_producer='real').for_stage('page_read')
assert pins['ocr_engine'] == 'paddleocr'
assert not {'paddle', 'paddleocr', 'torch', 'transformers'} & set(sys.modules)
"""
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True)
