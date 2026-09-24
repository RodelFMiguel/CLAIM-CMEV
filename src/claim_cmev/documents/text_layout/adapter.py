"""M4 adapter: page bytes in; validated ``DocumentPage`` records and artifact bytes out.

``run_page_reading`` performs no Kafka, database or object-store I/O: the consumer
wrapper verifies the envelope, applies the duplicate/superseded rules, stores the
returned artifacts, writes rows and publishes ``page_read_event_payload`` after commit.
A failure is never an empty success: every requested page yields a page reading with
``page_status`` and reasons, and a page with no text boxes is always ``unreadable``.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

from claim_cmev.contracts.common import (
    ArtifactRef,
    Provenance,
    Reason,
    deterministic_id,
    version_signature,
)
from claim_cmev.contracts.documents import DocumentPage, PageQuality, TextBox
from claim_cmev.contracts.documents import PageTransform as ContractPageTransform
from claim_cmev.vision.transforms import normalise_box, quad_to_box

from .config import PageReadingConfig
from .geometry import GeometryResult, correct_page_geometry, draw_boundary_debug
from .ocr import OcrEngine, OcrEngineError, OcrRegion, canonical_quad, check_granularity
from .page_transform import PageTransform
from .quality import FLAG_REASONS, image_flags, image_signals, text_signals
from .raster import PageFailure, RenderedPage, page_id_for, render_pages

M4_CODE_VERSION = "m4-text-layout/0.1.0"
PageStatus = Literal["complete", "partial", "unreadable"]
ProcessingStatus = Literal["succeeded", "partial", "failed"]
RETRYABLE_REASONS = frozenset({"ocr_failed", "artifact_hash_mismatch"})


@dataclass(frozen=True)
class PageFileInput:
    """The estimate file as read from the object store (FileRef plus its bytes)."""

    file_id: str
    media_type: str
    sha256: str
    data: bytes
    page_numbers: tuple[int, ...] | None = None  # None: every page, up to page.max_pages_per_job
    page_count: int | None = None


@dataclass(frozen=True)
class PageReadRequest:
    claim_id: str
    input_revision: int
    job_key: str
    file: PageFileInput
    versions: Mapping[str, str]
    provenance: Provenance | Mapping[str, Any]
    object_uri_prefix: str  # prepended to generated object keys, e.g. "s3://cmev-evidence/"


@dataclass(frozen=True)
class PageArtifact:
    artifact_id: str
    key: str
    object_uri: str
    media_type: str
    sha256: str
    byte_count: int
    data: bytes = field(repr=False)

    def ref(self) -> ArtifactRef:
        return ArtifactRef(artifact_id=self.artifact_id, object_uri=self.object_uri, sha256=self.sha256,
                           media_type=self.media_type, byte_count=self.byte_count)


@dataclass(frozen=True)
class PageReadResult:
    processing_status: ProcessingStatus
    reasons: tuple[Reason, ...]
    pages: tuple[DocumentPage, ...]  # pages that were rendered, whatever their status
    page_readings: tuple[dict[str, Any], ...]  # one per requested page, including unrendered failures
    artifacts: tuple[PageArtifact, ...]
    metrics: dict[str, Any]
    retryable: bool

    def unrendered_pages(self) -> list[dict[str, Any]]:
        """Pages that failed before a render existed; they have no DocumentPage."""
        rendered = {p.page_id for p in self.pages}
        return [r for r in self.page_readings if r["page_id"] not in rendered]


class _JobFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.reason = Reason(code=code, message=message)


def run_page_reading(request: PageReadRequest, engine: OcrEngine, config: PageReadingConfig,
                     clock: Callable[[], float] = time.perf_counter) -> PageReadResult:
    """Rasterise, correct, read and describe every requested page of one estimate file."""
    started = clock()
    provenance = request.provenance if isinstance(request.provenance, Provenance) \
        else Provenance.model_validate(request.provenance)
    try:
        versions = _merged_versions(request.versions, engine, config)
        if engine.info.source_kind == "fixture" and provenance.source_kind != "fixture":
            raise _JobFailure("fixture_provenance_mismatch", "a fixture OCR engine must be labelled fixture")
    except _JobFailure as failure:
        return PageReadResult("failed", (failure.reason,), (), (), (), {"total_ms": _ms(clock() - started)}, False)
    job = _Job(request, engine, config, provenance, versions, clock)
    if hashlib.sha256(request.file.data).hexdigest() != request.file.sha256:
        numbers = request.file.page_numbers or tuple(range(1, (request.file.page_count or 1) + 1))
        readings = tuple(job.failure_reading(n, "artifact_hash_mismatch", "file bytes do not match the recorded SHA-256")
                         for n in numbers)
        reason = Reason(code="artifact_hash_mismatch", message="estimate file failed its SHA-256 check")
        return PageReadResult("failed", (reason,), (), readings, (), {"total_ms": _ms(clock() - started)}, True)
    outcomes = render_pages(request.file.data, request.file.media_type, config.page.render_dpi,
                            config.page.max_pages_per_job, request.file.page_numbers, request.file.page_count)
    pages, readings, artifacts, page_metrics = [], [], [], []
    for outcome in outcomes:
        if isinstance(outcome, PageFailure):
            readings.append(job.failure_reading(outcome.page_number, outcome.reason, outcome.message))
            continue
        page, reading, page_artifacts, metrics = job.read_page(outcome)
        pages.append(page)
        readings.append(reading)
        artifacts.extend(page_artifacts)
        page_metrics.append(metrics)
    statuses = [r["page_status"] for r in readings]
    failed = [r for r in readings if r["page_status"] == "unreadable"]
    reasons = tuple(Reason(code="page_unreadable", message=f"page {r['page_number']} ({r['page_id']}) is unreadable: "
                           + ", ".join(x["code"] for x in r["reasons"])) for r in failed)
    status: ProcessingStatus = ("failed" if not statuses or all(s == "unreadable" for s in statuses)
                                else "partial" if failed else "succeeded")
    retryable = any(x["code"] in RETRYABLE_REASONS for r in failed for x in r["reasons"])
    return PageReadResult(status, reasons, tuple(pages), tuple(readings), tuple(artifacts),
                          {"total_ms": _ms(clock() - started), "pages": page_metrics}, retryable)


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


def _merged_versions(requested: Mapping[str, str], engine: OcrEngine, config: PageReadingConfig) -> dict[str, str]:
    """Request versions verbatim plus the engine, weights, config and code actually used."""
    actual = engine.info.version_entries() | {"page_config": config.config_version, "m4_code": M4_CODE_VERSION}
    aliases = {"preprocess_config_version": "page_config"}
    for key, value in requested.items():
        pinned = actual.get(aliases.get(key, key))
        if pinned is not None and value != pinned:
            raise _JobFailure("version_mismatch", f"request pins {key}={value!r} but this worker runs {pinned!r}")
    merged = dict(requested)
    for key, value in actual.items():
        merged.setdefault(key, value)
    return merged


def reading_order(quads: Sequence[np.ndarray], tolerance: float) -> list[int]:
    """Proposed display policy: bands by centre y within ``tolerance``, then left to right."""
    cy = [float(np.mean(q[:, 1])) for q in quads]
    x0 = [float(np.min(q[:, 0])) for q in quads]
    bands: list[list[int]] = []
    anchor: float | None = None
    for i in sorted(range(len(quads)), key=lambda k: (cy[k], x0[k])):
        if anchor is None or cy[i] - anchor > tolerance:
            bands.append([])
            anchor = cy[i]
        bands[-1].append(i)
    return [i for band in bands for i in sorted(band, key=lambda k: (x0[k], cy[k]))]


def _round_quad(points: np.ndarray) -> tuple[tuple[float, float], ...]:
    return tuple((round(float(x), 3), round(float(y), 3)) for x, y in np.asarray(points).reshape(-1, 2))


def _png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:  # pragma: no cover - OpenCV always encodes uint8 images
        raise RuntimeError("PNG encoding failed")
    return encoded.tobytes()


@dataclass
class _Job:
    request: PageReadRequest
    engine: OcrEngine
    config: PageReadingConfig
    provenance: Provenance
    versions: dict[str, str]
    clock: Callable[[], float]

    def _artifact(self, page_id: str, name: str, media_type: str, data: bytes) -> PageArtifact:
        r = self.request
        key = f"claims/{r.claim_id}/{r.input_revision}/pages/{page_id}/{version_signature(r.versions)}/{name}"
        return PageArtifact(deterministic_id("art", r.job_key, page_id, name), key, f"{r.object_uri_prefix}{key}",
                            media_type, hashlib.sha256(data).hexdigest(), len(data), data)

    def _base(self, page_id: str, page_number: int) -> dict[str, Any]:
        r = self.request
        return {"schema_version": "0.2.0", "claim_id": r.claim_id, "input_revision": r.input_revision,
                "job_key": r.job_key, "page_id": page_id, "file_id": r.file.file_id, "page_number": page_number,
                "extraction_route": "ocr", "code_version": M4_CODE_VERSION, "config_version": self.config.config_version,
                "config_sha256": self.config.sha256, "versions": self.versions,
                "provenance": self.provenance.model_dump(mode="json")}

    def failure_reading(self, page_number: int, code: str, message: str) -> dict[str, Any]:
        page_id = page_id_for(self.request.file.file_id, page_number)
        return self._base(page_id, page_number) | {
            "page_status": "unreadable", "reasons": [{"code": code, "message": message}],
            "render": None, "rectified": None, "boxes": []}

    def read_page(self, rendered: RenderedPage) -> tuple[DocumentPage, dict[str, Any], list[PageArtifact], dict]:
        cfg, info = self.config, self.engine.info
        page_id = page_id_for(self.request.file.file_id, rendered.page_number)
        t0 = self.clock()
        geometry = correct_page_geometry(rendered.image, cfg)
        t1 = self.clock()
        regions, failure = self._ocr(geometry.image)
        t2 = self.clock()
        transform = PageTransform.build(rendered.source, rendered.image.shape, geometry)
        signals = image_signals(geometry.image, geometry.source_page_width_px, cfg.quality)
        flags = image_flags(signals, cfg.quality)
        reasons: list[tuple[str, str]] = []
        partial = False
        granularity = check_granularity(regions or [], info.granularity, cfg.page.expected_text_box_granularity,
                                        cfg.ocr.nested_box_containment)
        boxes: list[dict[str, Any]] = []
        if failure is not None:
            reasons.append(failure)
        elif not regions:
            reasons.append(("ocr_no_text", "the engine returned no text boxes; the page is not an empty estimate"))
        elif not granularity.ok:
            reasons.append(("granularity_mismatch", granularity.message))
            reasons.append((granularity.detail or "granularity_mismatch", granularity.message))
        else:
            boxes = self._boxes(page_id, regions, geometry, transform)
        unreadable = bool(reasons)
        confidences = [b["confidence"] for b in boxes]
        text = text_signals(confidences, cfg.ocr.min_box_confidence)
        if boxes:
            if text.confidence_count == 0:
                reasons.append(("confidence_unavailable", "no box carries an engine confidence"))
                partial = True
            elif text.low_confidence_fraction > cfg.ocr.max_low_confidence_fraction:
                reasons.append(("low_confidence_fraction",
                                f"{text.low_confidence_count} of {text.confidence_count} boxes below "
                                f"{cfg.ocr.min_box_confidence}"))
                partial = True
            elif text.low_confidence_count:
                reasons.append(("low_confidence_region", f"{text.low_confidence_count} box(es) flagged"))
            if any("transform_round_trip_failed" in b["flags"] for b in boxes):
                reasons.append(("transform_round_trip_failed", "a box does not map back within tolerance"))
                partial = True
        if geometry.skew_uncorrected:
            reasons.append(("skew_not_corrected", f"boundary unreliable and skew {geometry.skew.angle_deg} deg "
                            f"exceeds page.max_deskew_angle_deg"))
            partial = True
        for flag in flags:
            reasons.append((FLAG_REASONS[flag], f"quality screen: {flag}"))
            partial = partial or flag in cfg.quality.partial_on_flags
        status: PageStatus = "unreadable" if unreadable else "partial" if partial else "complete"
        return self._records(rendered, page_id, geometry, transform, signals, flags, text, granularity, boxes,
                             status, reasons, {"page_id": page_id, "geometry_ms": _ms(t1 - t0),
                                               "ocr_ms": _ms(t2 - t1), "box_count": len(boxes)})

    def _ocr(self, image: np.ndarray) -> tuple[list[OcrRegion] | None, tuple[str, str] | None]:
        """Run the engine and validate its regions; geometry is reordered, never altered."""
        try:
            regions = list(self.engine.read(image))
        except OcrEngineError as exc:
            return None, (exc.reason_code, str(exc))
        except Exception as exc:  # an engine crash is a page failure, never an empty page
            return None, ("ocr_failed", f"OCR engine raised {type(exc).__name__}")
        try:
            checked = []
            for region in regions:
                if region.confidence is not None and not 0.0 <= region.confidence <= 1.0:
                    raise OcrEngineError("ocr_output_invalid", f"confidence {region.confidence!r} outside [0, 1]")
                checked.append(OcrRegion(canonical_quad(region.quad), str(region.text), region.confidence,
                                         region.level))
        except OcrEngineError as exc:
            return None, (exc.reason_code, str(exc))
        return checked, None

    def _boxes(self, page_id: str, regions: Sequence[OcrRegion], geometry: GeometryResult,
               transform: PageTransform) -> list[dict[str, Any]]:
        cfg = self.config
        quads = [np.asarray(r.quad, dtype=float) for r in regions]
        tolerance = cfg.page.row_band_tolerance_px * geometry.width / cfg.page.rectified_width_px
        margin = cfg.transforms.round_trip_tolerance_px
        boxes = []
        for order_index, i in enumerate(reading_order(quads, tolerance)):
            region, quad = regions[i], quads[i]
            flags = []
            confidence = region.confidence
            if confidence is not None and confidence < cfg.ocr.min_box_confidence:
                flags.append("low_confidence_region")
            if (quad.min() < -margin or quad[:, 0].max() > geometry.width + margin
                    or quad[:, 1].max() > geometry.height + margin):
                flags.append("outside_page_bounds")
            if transform.round_trip_error(quad) > margin:
                flags.append("transform_round_trip_failed")
            boxes.append({
                "box_id": deterministic_id("bx", self.request.job_key, page_id, order_index),
                "order_index": order_index,
                "text": region.text,
                "confidence": None if confidence is None else float(confidence),
                "confidence_reason": "engine_supplies_no_confidence" if confidence is None else None,
                "granularity": region.level,
                "quad_rectified": _round_quad(quad),
                "box_norm": tuple(round(v, 6) for v in normalise_box(quad_to_box(quad), geometry.width, geometry.height)),
                "quad_original": _round_quad(transform.rectified_to_original_points(quad)),
                "quad_source": _round_quad(transform.rectified_to_source_points(quad)),
                "flags": flags,
            })
        return boxes

    def _records(self, rendered: RenderedPage, page_id: str, geometry: GeometryResult, transform: PageTransform,
                 signals, flags, text, granularity, boxes, status: PageStatus, reasons, metrics):
        cfg, info = self.config, self.engine.info
        render = self._artifact(page_id, "render.png", "image/png", _png(rendered.image))
        rectified = self._artifact(page_id, "rectified.png", "image/png", _png(geometry.image))
        artifacts = [render, rectified]
        if cfg.page.write_debug:
            artifacts.append(self._artifact(page_id, "boundary_debug.png", "image/png",
                                            _png(draw_boundary_debug(rendered.image, geometry.boundary))))
        actual = granularity.actual or info.granularity
        b = geometry.boundary
        reading = self._base(page_id, rendered.page_number) | {
            "page_status": status,
            "reasons": [{"code": c, "message": m} for c, m in reasons],
            "render": {"artifact_id": render.artifact_id, "object_uri": render.object_uri, "sha256": render.sha256},
            "rectified": {"artifact_id": rectified.artifact_id, "object_uri": rectified.object_uri,
                          "sha256": rectified.sha256, "copy_of_render": geometry.kind == "none"},
            "geometry": {
                "kind": geometry.kind, "reason": geometry.reason, "correction_applied": geometry.correction_applied,
                "rotation_deg": geometry.rotation_deg, "skew_uncorrected": geometry.skew_uncorrected,
                "boundary": {"found": b.found, "reliable": b.reliable, "reason": b.reason,
                             "quad": None if b.quad is None else _round_quad(b.quad),
                             "area_fraction": b.area_fraction, "corner_angles_deg": b.corner_angles_deg,
                             "contrast": b.contrast},
                "skew": None if geometry.skew is None else {
                    "reliable": geometry.skew.reliable, "reason": geometry.skew.reason,
                    "angle_deg": geometry.skew.angle_deg, "peak_ratio": geometry.skew.peak_ratio},
            },
            "transform": transform.to_dict(),
            "ocr": {"engine": info.engine, "engine_version": info.engine_version, "lang": info.lang,
                    "weights": dict(info.weights), "declared_granularity": info.granularity,
                    "expected_granularity": cfg.page.expected_text_box_granularity,
                    "actual_granularity": granularity.actual, "granularity_check": granularity.message},
            "quality": {"state": status, "flags": flags, "image": signals.to_dict(), "text": text.to_dict()},
            "boxes": [dict(bx, quad_rectified=list(map(list, bx["quad_rectified"])),
                           quad_original=list(map(list, bx["quad_original"])),
                           quad_source=list(map(list, bx["quad_source"])), box_norm=list(bx["box_norm"]))
                      for bx in boxes],
        }
        reading_bytes = json.dumps(reading, sort_keys=True, separators=(",", ":")).encode()
        reading_artifact = self._artifact(page_id, "page_reading.json", "application/json", reading_bytes)
        artifacts.append(reading_artifact)
        page = DocumentPage(
            claim_id=self.request.claim_id, input_revision=self.request.input_revision,
            provenance=self.provenance, versions=self.versions, page_id=page_id,
            file_id=self.request.file.file_id, page_number=rendered.page_number,
            corrected_render_ref=rectified.ref(), page_reading_ref=reading_artifact.ref(),
            source_render_ref=render.ref(),
            transform=ContractPageTransform(
                source_width=transform.render_width, source_height=transform.render_height,
                corrected_width=transform.rectified_width, corrected_height=transform.rectified_height,
                rotation_degrees=geometry.rotation_deg, exif_orientation=rendered.source.exif_orientation,
                render_scale=rendered.source.render_scale, geometry_correction=geometry.kind,
                correction_reason=geometry.reason,
                homography=tuple(tuple(float(v) for v in row) for row in geometry.homography),
                homography_inverse=tuple(tuple(float(v) for v in row) for row in geometry.inverse)),
            extraction_route="ocr", text_granularity=actual,
            text_boxes=[TextBox(**{k: v for k, v in bx.items() if k != "quad_source"}) for bx in boxes],
            mean_text_confidence=text.mean_confidence,
            mean_text_confidence_reason=None if text.mean_confidence is not None else (
                "engine_supplies_no_confidence" if boxes else "no_text_boxes"),
            quality=PageQuality(state=status, reasons=list(dict.fromkeys(c for c, _ in reasons)),
                                box_count=len(boxes), mean_confidence=text.mean_confidence,
                                low_confidence_fraction=text.low_confidence_fraction,
                                sharpness=signals.sharpness, clipped_fraction=signals.clipped_pixel_fraction),
        )
        return page, reading, artifacts, metrics


def page_read_event_payload(page: DocumentPage) -> dict[str, Any]:
    """Payload for ``cmev.evt.page-read.v1`` (integration contracts 5.4), one page."""
    return {
        "page_id": page.page_id,
        "page_number": page.page_number,
        "corrected_render_ref": page.corrected_render_ref.model_dump(mode="json"),
        "page_reading_ref": page.page_reading_ref.model_dump(mode="json"),
        "transform": page.transform.model_dump(mode="json"),
        "text_granularity": page.text_granularity,
        "token_count": len(page.text_boxes),
        "mean_text_confidence": page.mean_text_confidence,
        "mean_text_confidence_reason": page.mean_text_confidence_reason,
        "page_quality": {"state": page.quality.state, "reasons": list(page.quality.reasons)},
        "correction_applied": page.transform.perspective_applied,
    }
