"""PaddleOCR engine wrapper: lazy import, pinned versions, weights required up front.

The default test run has no Paddle installed and never imports it. The real-engine test
runs only where ``paddleocr`` is importable and ``CMEV_M4_OCR_WEIGHTS`` names a weights
root (the private smoke-test venv under runtime/venvs/ocr).
"""
import os
import sys

import numpy as np
import pytest

from claim_cmev.documents.text_layout import OcrEngine, OcrEngineError, StubOcrEngine
from claim_cmev.documents.text_layout.paddle import (
    PINNED_PADDLEOCR_VERSION,
    PINNED_PADDLEPADDLE_VERSION,
    WEIGHT_SUBDIRS,
    PaddleOcrEngine,
)


def test_module_import_does_not_import_paddle():
    assert PINNED_PADDLEOCR_VERSION == "2.10.0" and PINNED_PADDLEPADDLE_VERSION == "2.6.2"
    if "paddleocr" not in sys.modules:  # nothing in this package imported it
        assert "paddle" not in sys.modules


def test_missing_weights_fail_at_construction(tmp_path):
    with pytest.raises(OcrEngineError) as error:
        PaddleOcrEngine(tmp_path)
    assert error.value.reason_code == "ocr_weights_missing"


def test_uninstalled_engine_is_reported_not_silently_replaced(tmp_path, monkeypatch):
    for sub in WEIGHT_SUBDIRS.values():
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "inference.pdiparams").write_bytes(b"fake")
    monkeypatch.setattr("claim_cmev.documents.text_layout.paddle.installed_versions",
                        lambda: {"paddleocr": None, "paddlepaddle": None})
    with pytest.raises(OcrEngineError) as error:
        PaddleOcrEngine(tmp_path)
    assert error.value.reason_code == "ocr_engine_unavailable"
    monkeypatch.setattr("claim_cmev.documents.text_layout.paddle.installed_versions",
                        lambda: {"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"})
    with pytest.raises(OcrEngineError) as error:
        PaddleOcrEngine(tmp_path)
    assert error.value.reason_code == "ocr_version_mismatch"


@pytest.mark.parametrize("overrides, code", [
    ({"ocr": {"engine_version": "2.7.3"}}, "ocr_version_mismatch"),
    ({"page": {"expected_text_box_granularity": "word"}}, "granularity_mismatch"),
])
def test_start_up_refuses_a_configuration_this_build_does_not_serve(tmp_path, overrides, code):
    from claim_cmev.documents.text_layout import load_page_reading_config

    with pytest.raises(OcrEngineError) as error:
        PaddleOcrEngine.from_config(tmp_path, load_page_reading_config().with_overrides(overrides))
    assert error.value.reason_code == code


def test_stub_engine_satisfies_the_protocol():
    assert isinstance(StubOcrEngine(), OcrEngine)
    assert StubOcrEngine().info.source_kind == "fixture"


@pytest.mark.skipif(not os.getenv("CMEV_M4_OCR_WEIGHTS"), reason="real PaddleOCR smoke only in the OCR venv")
def test_real_paddleocr_returns_line_regions_with_confidence():
    pytest.importorskip("paddleocr")
    import cv2

    engine = PaddleOcrEngine(os.environ["CMEV_M4_OCR_WEIGHTS"])
    image = np.full((120, 900, 3), 255, np.uint8)
    cv2.putText(image, "FRT BUMPER        980.00", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    regions = engine.read(image)
    assert regions and {r.level for r in regions} == {"line"}
    assert all(r.confidence is not None and 0 <= r.confidence <= 1 for r in regions)
