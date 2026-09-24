"""Pinned PaddleOCR engine behind the ``OcrEngine`` protocol.

``paddleocr`` is imported lazily inside the constructor, so this module imports on a
machine without Paddle. Weights must already be on disk (baked into the image or
mounted read-only); downloading is opt-in and meant only for the smoke test. The
granularity this configuration returns was established by the 2026-09-24 synthetic
smoke test (``artifacts/evaluation/m4-ocr-smoke/``): text-line segments, i.e. ``line``.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .ocr import Granularity, OcrEngineError, OcrEngineInfo, OcrRegion

if TYPE_CHECKING:
    from .config import PageReadingConfig

PINNED_PADDLEOCR_VERSION = "2.10.0"
PINNED_PADDLEPADDLE_VERSION = "2.6.2"
DEFAULT_WEIGHT_NAMES = {
    "detection": "en_PP-OCRv3_det_infer",
    "recognition": "en_PP-OCRv4_rec_infer",
    "angle": "ch_ppocr_mobile_v2.0_cls_infer",
}
WEIGHT_SUBDIRS = {"detection": "det", "recognition": "rec", "angle": "cls"}
PADDLE_GRANULARITY: Granularity = "line"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for name in ("paddleocr", "paddlepaddle"):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


class PaddleOcrEngine:
    """CPU PaddleOCR 2.x reader; one instance per container, created at start-up."""

    def __init__(self, weights_root: str | Path, *, lang: str = "en", use_angle_cls: bool = True,
                 weight_names: Mapping[str, str] | None = None, allow_download: bool = False):
        root = Path(weights_root)
        dirs = {kind: root / sub for kind, sub in WEIGHT_SUBDIRS.items()}
        missing = [kind for kind, d in dirs.items() if not (d / "inference.pdiparams").is_file()]
        if missing and not allow_download:
            raise OcrEngineError("ocr_weights_missing", f"no weights for {', '.join(missing)} under {root}")
        versions = installed_versions()
        if versions["paddleocr"] is None or versions["paddlepaddle"] is None:
            raise OcrEngineError("ocr_engine_unavailable", "paddleocr/paddlepaddle are not installed")
        if (versions["paddleocr"], versions["paddlepaddle"]) != (PINNED_PADDLEOCR_VERSION, PINNED_PADDLEPADDLE_VERSION):
            raise OcrEngineError("ocr_version_mismatch", f"installed {versions}, pinned paddleocr "
                                 f"{PINNED_PADDLEOCR_VERSION} / paddlepaddle {PINNED_PADDLEPADDLE_VERSION}")
        from paddleocr import PaddleOCR  # lazy: heavy import, absent from the default test venv

        self._ocr = PaddleOCR(use_angle_cls=use_angle_cls, lang=lang, use_gpu=False, show_log=False,
                              det_model_dir=str(dirs["detection"]), rec_model_dir=str(dirs["recognition"]),
                              cls_model_dir=str(dirs["angle"]))
        self._use_angle_cls = use_angle_cls
        names = dict(DEFAULT_WEIGHT_NAMES, **(weight_names or {}))
        weights = {kind: f"{names[kind]}@sha256:{_file_digest(d / 'inference.pdiparams')[:16]}"
                   for kind, d in dirs.items()}
        self._info = OcrEngineInfo("paddleocr", PINNED_PADDLEOCR_VERSION, PADDLE_GRANULARITY, lang, weights, "real")

    @classmethod
    def from_config(cls, weights_root: str | Path, config: "PageReadingConfig") -> "PaddleOcrEngine":
        """Container start-up: refuse to start unless the configuration pins this engine."""
        if (config.ocr.engine, config.ocr.engine_version) != ("paddleocr", PINNED_PADDLEOCR_VERSION):
            raise OcrEngineError("ocr_version_mismatch", f"configuration pins {config.ocr.engine} "
                                 f"{config.ocr.engine_version}; this build ships paddleocr {PINNED_PADDLEOCR_VERSION}")
        if config.page.expected_text_box_granularity != PADDLE_GRANULARITY:
            raise OcrEngineError("granularity_mismatch", "configuration expects "
                                 f"{config.page.expected_text_box_granularity!r}; PaddleOCR returns line regions")
        return cls(weights_root, lang=config.ocr.lang, use_angle_cls=config.ocr.use_angle_cls)

    @property
    def info(self) -> OcrEngineInfo:
        return self._info

    def read(self, image: np.ndarray) -> Sequence[OcrRegion]:
        """Engine-native detections: four-point quad, text and recognition score."""
        result = self._ocr.ocr(image, cls=self._use_angle_cls)
        detections = result[0] if result else None
        regions = []
        for quad, (text, score) in detections or []:
            regions.append(OcrRegion(tuple((float(x), float(y)) for x, y in quad), str(text),
                                     float(score), PADDLE_GRANULARITY))
        return regions
