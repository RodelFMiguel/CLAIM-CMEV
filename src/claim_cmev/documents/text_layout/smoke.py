"""OCR smoke test on SYNTHETIC estimate pages (M4 day-2 task, partially).

This is not the specified day-2 test: that needs three real photographed pages. It
establishes, for the pinned engine, the text-box granularity actually returned, whether
confidence is present, and per-page latency and peak memory on this machine. Run it in
the private OCR venv, never in the application venv::

    LD_LIBRARY_PATH=runtime/venvs/ocr/_support/syslibs/extracted/usr/lib/x86_64-linux-gnu \
    PYTHONPATH=src runtime/venvs/ocr/bin/python \
        -m claim_cmev.documents.text_layout.smoke \
        --weights runtime/venvs/ocr/weights --out artifacts/evaluation/m4-ocr-smoke

Each page runs in a fresh subprocess, so peak resident memory is per page.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np

from .config import load_page_reading_config
from .synthetic import (
    add_pen_strokes,
    camera_corners,
    photograph,
    render_estimate_page,
    rotate_within_frame,
)

PAGES = {
    "flat_scan": "A4 printed estimate rendered at 300 dpi (2480x3508), no distortion",
    "perspective_photo": "same page photographed by a pinhole camera, 3024x4032, pitch 22, yaw -12, roll 3 deg",
    "rotated_blurred_scan": "300 dpi page skewed 5 deg with no visible page edge, Gaussian blur sigma 1.5",
    "obscured_print_extra": "extra: flat page with blue pen strokes over three printed amounts",
}
CLAIM_ID = "01J8Z3N4V6W8X0Y2Z4A6B8C0D2"


def build_page(name: str) -> tuple[np.ndarray, list[str]]:
    page = render_estimate_page(2480)
    truth = [line.text for line in page.lines]
    if name == "flat_scan":
        return page.image, truth
    if name == "perspective_photo":
        size = (3024, 4032)
        corners = camera_corners(page, size, pitch_deg=22, yaw_deg=-12, roll_deg=3, fill=0.75)
        return photograph(page, corners, size, seed=11)[0], truth
    if name == "rotated_blurred_scan":
        return cv2.GaussianBlur(rotate_within_frame(page, 5.0)[0], (0, 0), 1.5), truth
    if name == "obscured_print_extra":
        amounts = [line.box for line in page.lines if line.column == "amount" and line.role == "cell"]
        return add_pen_strokes(page.image, amounts[:3]), truth
    raise KeyError(name)


def _png(image: np.ndarray) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).save(buffer, "PNG")
    return buffer.getvalue()


def run_one(name: str, weights: Path, out_dir: Path) -> dict:
    from claim_cmev.contracts.common import make_job_key

    from .adapter import PageFileInput, PageReadRequest, run_page_reading
    from .paddle import PaddleOcrEngine

    started = time.perf_counter()
    engine = PaddleOcrEngine(weights)
    load_s = time.perf_counter() - started
    warm = np.full((96, 640, 3), 255, np.uint8)
    cv2.putText(warm, "WARM UP 123.45", (20, 64), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    engine.read(warm)
    image, truth = build_page(name)
    data = _png(image)
    versions = {"code": "m4-ocr-smoke", "taxonomy": "none"}
    request = PageReadRequest(
        claim_id=CLAIM_ID, input_revision=1, job_key=make_job_key(CLAIM_ID, 1, "page_read", versions),
        file=PageFileInput(file_id=f"synthetic-{name}", media_type="image/png",
                           sha256=hashlib.sha256(data).hexdigest(), data=data),
        versions=versions, object_uri_prefix="file://artifacts/evaluation/m4-ocr-smoke/",
        provenance={"source_kind": "synthetic", "runtime_profile": "lean", "producer_service": "m4-ocr-smoke"})
    config = load_page_reading_config()
    t0 = time.perf_counter()
    result = run_page_reading(request, engine, config)
    total_s = time.perf_counter() - t0
    [page], [reading] = result.pages, result.page_readings
    rectified = next(a for a in result.artifacts if a.key.endswith("rectified.png"))
    rectified_image = cv2.imdecode(np.frombuffer(rectified.data, np.uint8), cv2.IMREAD_COLOR)
    repeats = []
    for _ in range(2):
        t = time.perf_counter()
        engine.read(rectified_image)
        repeats.append(time.perf_counter() - t)
    ocr_first = result.metrics["pages"][0]["ocr_ms"] / 1000.0
    texts = [b.text for b in page.text_boxes]
    confidences = [b.confidence for b in page.text_boxes]
    multi_word = [t for t in texts if " " in t.strip()]
    truth_set = set(truth)
    exact = sorted(t for t in truth_set if t in texts)
    merged = [t for t in texts if sum(1 for s in truth_set if s and s in t and s != t) >= 2]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.page_reading.json").write_text(json.dumps(reading, indent=1, sort_keys=True))
    preview = cv2.resize(rectified_image, None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out_dir / f"{name}.rectified_preview.png"), preview)
    return {
        "page": name, "description": PAGES[name], "input_width": image.shape[1], "input_height": image.shape[0],
        "engine_load_s": round(load_s, 2),
        "page_total_s": round(total_s, 2),
        "geometry_s": round(result.metrics["pages"][0]["geometry_ms"] / 1000.0, 3),
        "ocr_latency_s": {"first": round(ocr_first, 2), "repeats": [round(r, 2) for r in repeats],
                          "median_of_3": round(statistics.median([ocr_first, *repeats]), 2)},
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        "geometry_correction": page.transform.geometry_correction,
        "correction_reason": page.transform.correction_reason,
        "rectified_size": [page.transform.corrected_width, page.transform.corrected_height],
        "page_status": page.quality.state, "page_reasons": list(page.quality.reasons),
        "text_granularity_recorded": page.text_granularity,
        "granularity_check": reading["ocr"]["granularity_check"],
        "box_count": len(texts), "multi_word_boxes": len(multi_word),
        "boxes_merging_two_or_more_truth_strings": merged,
        "truth_strings": len(truth_set), "truth_strings_read_exactly": len(exact),
        "truth_strings_missed_or_misread": sorted(truth_set - set(exact)),
        "confidence_present": all(c is not None for c in confidences),
        "confidence_min": min(confidences) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
        "low_confidence_boxes": [{"text": b.text, "confidence": round(b.confidence, 3)}
                                 for b in page.text_boxes if "low_confidence_region" in b.flags],
        "boxes": [{"order": b.order_index, "text": b.text, "confidence": round(b.confidence, 4),
                   "box_norm": b.box_norm} for b in page.text_boxes],
    }


def _environment(weights: Path) -> dict:
    import importlib.metadata as md

    from .paddle import DEFAULT_WEIGHT_NAMES, WEIGHT_SUBDIRS

    def version(name: str) -> str | None:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return None

    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.processor())
    weights_info = {}
    for kind, sub in WEIGHT_SUBDIRS.items():
        params = weights / sub / "inference.pdiparams"
        weights_info[kind] = {"name": DEFAULT_WEIGHT_NAMES[kind],
                              "sha256_pdiparams": hashlib.sha256(params.read_bytes()).hexdigest(),
                              "bytes": params.stat().st_size}
    return {
        "python": platform.python_version(), "platform": platform.platform(), "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "packages": {n: version(n) for n in ("paddleocr", "paddlepaddle", "numpy", "opencv-python",
                                             "opencv-contrib-python", "opencv-python-headless", "pillow",
                                             "pydantic", "setuptools")},
        "weights": weights_info,
        "engine_settings": {"lang": "en", "use_angle_cls": True, "use_gpu": False,
                            "other": "PaddleOCR 2.10 defaults (det_limit_side_len 960, cpu_threads 10, mkldnn off)"},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--one")
    args = parser.parse_args(argv)
    if args.one:
        print(json.dumps(run_one(args.one, args.weights, args.out)))
        return 0
    results, failures = [], []
    for name in PAGES:
        proc = subprocess.run([sys.executable, "-m", __spec__.name, "--weights", str(args.weights),
                               "--out", str(args.out), "--one", name], capture_output=True, text=True)
        if proc.returncode:
            failures.append({"page": name, "returncode": proc.returncode, "stderr_tail": proc.stderr[-2000:]})
        else:
            results.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    report = {
        "status": "SYNTHETIC pages only - not the three real photographed pages the M4 day-2 task requires",
        "date": time.strftime("%Y-%m-%d"), "environment": _environment(args.weights),
        "config_version": load_page_reading_config().config_version, "pages": results, "failures": failures,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "pages"}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
