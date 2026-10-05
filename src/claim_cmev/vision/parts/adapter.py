"""Online serving adapter for M1 Vehicle Part Segmentation.

Loads the trained SegFormer checkpoint, decodes vehicle photographs with EXIF
orientation, prepares the 512x512 model frame, runs inference, saves paletted mask PNGs
and display overlays to object storage, and produces PartPrediction and ImageQuality records
conforming to CLAIM-CMEV data and integration contracts.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/integration_contracts.md section 5.4
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import io
import logging
from pathlib import Path
import time
from typing import Any, Literal

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
from transformers import SegformerConfig, SegformerForSemanticSegmentation

from claim_cmev.contracts.common import (
    ArtifactRef,
    Provenance,
    Reason,
    deterministic_id,
    version_signature,
)
from claim_cmev.contracts.imaging import (
    ImageQuality,
    ImageTransform,
    MaskRef,
    PartPrediction,
)
from claim_cmev.messaging.consumer import Context, PermanentError
from claim_cmev.persistence.store import insert_records
from claim_cmev.review.overlays import render_photo_overlay
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE, PART_CODE_TO_ID, build_palette
from claim_cmev.vision.transforms import decode_oriented, letterbox, plan_model_frame

from .config import PartsConfig, load_parts_config

log = logging.getLogger("cmev.vision.parts.adapter")

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

ProcessingStatus = Literal["succeeded", "partial", "failed"]


@dataclass(frozen=True)
class PhotoFileInput:
    """A photo file input for batch segmentation."""

    file_id: str
    media_type: str
    sha256: str
    data: bytes
    exif_orientation: int = 1


@dataclass(frozen=True)
class PartsSegmentRequest:
    """Request for M1 batch vehicle part segmentation."""

    claim_id: str
    input_revision: int
    job_key: str
    photos: tuple[PhotoFileInput, ...]
    versions: Mapping[str, str]
    provenance: Provenance | Mapping[str, Any]
    object_uri_prefix: str = "s3://cmev-evidence/"


@dataclass(frozen=True)
class PartArtifact:
    """Artifact produced by M1 segmentation (mask PNG or display overlay PNG)."""

    artifact_id: str
    key: str
    object_uri: str
    media_type: str
    sha256: str
    byte_count: int
    data: bytes = field(repr=False)

    def ref(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            object_uri=self.object_uri,
            sha256=self.sha256,
            media_type=self.media_type,
            byte_count=self.byte_count,
        )


@dataclass(frozen=True)
class PhotoSegmentationOutcome:
    """Result of segmenting a single photo."""

    photo_id: str
    status: Literal["succeeded", "failed"]
    reasons: tuple[Reason, ...]
    predictions: tuple[PartPrediction, ...]
    quality: ImageQuality | None
    mask_ref: MaskRef | None
    transform: ImageTransform | None
    artifacts: tuple[PartArtifact, ...]


@dataclass(frozen=True)
class PartsSegmentResult:
    """Batch result of M1 segmentation conforming to module specification."""

    processing_status: ProcessingStatus
    reasons: tuple[Reason, ...]
    photo_outcomes: tuple[PhotoSegmentationOutcome, ...]
    predictions: tuple[PartPrediction, ...]
    qualities: tuple[ImageQuality, ...]
    artifacts: tuple[PartArtifact, ...]
    metrics: dict[str, Any]
    retryable: bool


class PartsSegmenter:
    """Inference engine for M1 vehicle part segmentation."""

    def __init__(
        self,
        model_dir: str | Path = "artifacts/models/parts/0.1.0",
        config: PartsConfig | None = None,
        device_str: str | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.config = config or load_parts_config()

        # Resolve device cleanly with CPU fallback if CUDA is requested but unavailable
        if device_str:
            self.device = torch.device(device_str)
        elif self.config.device == "cuda":
            if torch.cuda.is_available():
                self.device = torch.device("cuda")
            else:
                log.warning("CUDA requested in configuration but not available. Falling back to CPU.")
                self.device = torch.device("cpu")
        elif self.config.device == "cpu":
            self.device = torch.device("cpu")
        else:  # "auto" or unspecified
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        if not (self.model_dir / "config.json").exists():
            raise FileNotFoundError(f"Model config not found in: {self.model_dir}")

        log.info(f"Loading SegFormer model from {self.model_dir} to {self.device}...")
        self.seg_config = SegformerConfig.from_pretrained(self.model_dir)
        self.model = SegformerForSemanticSegmentation.from_pretrained(
            self.model_dir,
            config=self.seg_config,
        ).to(self.device)
        self.model.eval()

        # Build ID to code mapping from model config
        self.id_to_part_code = {int(k): v for k, v in self.seg_config.id2label.items()}

    def segment_photo(
        self,
        photo_bytes: bytes,
        photo_id: str,
        claim_id: str,
        input_revision: int,
        job_key: str,
        versions: Mapping[str, str],
        provenance: Mapping[str, Any],
        storage: Storage,
    ) -> tuple[list[PartPrediction], ImageQuality, MaskRef, ImageTransform]:
        """Run segmentation on one photo and persist mask artifact to storage."""
        # 1. Decode photo with EXIF orientation
        try:
            oriented_img, orientation, (sw, sh) = decode_oriented(photo_bytes)
        except Exception as exc:
            raise PermanentError("corrupt_photo", f"Failed to decode photo {photo_id}: {exc}") from exc

        # 2. Plan model frame transform (longest_edge_pad)
        transform_spec = plan_model_frame(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            frame_size=self.config.input_size,
            policy=self.config.resize_policy,
        )

        # 3. Letterbox image
        letterboxed_img = letterbox(oriented_img, transform_spec, pad_value=0)

        # 4. Normalize and create PyTorch tensor
        img_float = letterboxed_img.astype(np.float32) / 255.0
        normalized = (img_float - IMAGENET_MEAN) / IMAGENET_STD
        tensor = torch.from_numpy(normalized).permute(2, 0, 1).unsqueeze(0).float().to(self.device)

        # 5. Run inference
        is_cuda = self.device.type == "cuda"
        with torch.no_grad():
            with torch.amp.autocast(device_type="cuda" if is_cuda else "cpu", enabled=is_cuda):
                outputs = self.model(pixel_values=tensor)
                logits = outputs.logits

            upsampled = F.interpolate(
                logits,
                size=(self.config.input_size, self.config.input_size),
                mode="bilinear",
                align_corners=False,
            )
            probs = F.softmax(upsampled, dim=1)[0]  # shape: (num_classes, H, W)
            preds = probs.argmax(dim=0).cpu().numpy().astype(np.uint8)

        # 6. Save paletted mask PNG to storage
        mask_pil = Image.fromarray(preds, mode="P")
        mask_pil.putpalette(build_palette())

        buf = io.BytesIO()
        mask_pil.save(buf, format="PNG")
        mask_bytes = buf.getvalue()
        mask_sha256 = hashlib.sha256(mask_bytes).hexdigest()

        # Both direct path and versioned path preserved to avoid collision on rerun
        v_sig = version_signature(versions)
        mask_key = f"claims/{claim_id}/{input_revision}/parts/{photo_id}/mask.png"
        versioned_mask_key = f"claims/{claim_id}/{input_revision}/parts/{photo_id}/{v_sig}/mask.png"
        storage.write(mask_key, mask_bytes, "image/png")
        storage.write(versioned_mask_key, mask_bytes, "image/png")
        mask_uri = storage.uri(mask_key)

        mask_ref = MaskRef(
            artifact_id=deterministic_id("pm", job_key, photo_id),
            object_uri=mask_uri,
            sha256=mask_sha256,
            width=self.config.input_size,
            height=self.config.input_size,
            encoding="class_index_png",
            source_photo_id=photo_id,
        )

        # 7. Construct ImageTransform
        image_transform = ImageTransform(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            model_width=self.config.input_size,
            model_height=self.config.input_size,
            scale=transform_spec.scale,
            pad_left=float(transform_spec.pad_x),
            pad_top=float(transform_spec.pad_y),
            mask_frame="model",
        )

        # 8. Optional display overlay generation
        if self.config.write_overlay:
            try:
                overlay_bytes = render_photo_overlay(
                    photo_image=photo_bytes,
                    photo_id=photo_id,
                    part_masks={"parts": mask_bytes},
                    transform=image_transform,
                    apply_exif=True,
                    show_damage=False,
                    show_parts=True,
                )
                overlay_key = f"claims/{claim_id}/{input_revision}/parts/{photo_id}/overlay.png"
                storage.write(overlay_key, overlay_bytes, "image/png")
            except Exception as exc:
                log.warning(f"Failed to generate overlay for {photo_id}: {exc}")

        # 9. Build PartPrediction rows
        predictions: list[PartPrediction] = []
        for class_id, part_code in self.id_to_part_code.items():
            if class_id == 0 or part_code == "background":
                continue

            class_mask = (preds == class_id)
            pixel_count = int(class_mask.sum())
            if pixel_count <= 0:
                continue

            mean_conf = float(probs[class_id, class_mask].mean().item())
            accepted = (pixel_count >= self.config.min_part_pixels) and (mean_conf >= self.config.min_part_confidence)

            pred = PartPrediction(
                prediction_id=deterministic_id("pp", job_key, part_code),
                photo_id=photo_id,
                part_code=part_code,
                side="unknown",
                mask_ref=mask_ref,
                mean_confidence=round(mean_conf, 4),
                pixel_count=pixel_count,
                accepted=accepted,
                transform=image_transform,
                claim_id=claim_id,
                input_revision=input_revision,
                versions=dict(versions),
                provenance=Provenance.model_validate(provenance),
            )
            predictions.append(pred)

        # 10. Honest quality reporting: unmeasured attributes report not_assessed
        quality_state: Literal["acceptable", "limited", "unusable", "not_assessed"] = "not_assessed"
        reasons: list[str] = ["quality_not_assessed"]
        if sw < 200 or sh < 200:
            quality_state = "limited"
            reasons = ["resolution_low"]

        quality = ImageQuality(
            claim_id=claim_id,
            input_revision=input_revision,
            photo_id=photo_id,
            state=quality_state,
            blur_score=None,
            exposure_state="not_assessed",
            reasons=reasons,
            config_version=self.config.config_version,
            versions=dict(versions),
            provenance=Provenance.model_validate(provenance),
        )

        return predictions, quality, mask_ref, image_transform


def run_parts_segmentation(
    request: PartsSegmentRequest,
    segmenter: PartsSegmenter,
    config: PartsConfig | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> PartsSegmentResult:
    """Pure adapter entry point processing a batch of photos for M1."""
    started = clock()
    cfg = config or segmenter.config
    prov = (
        request.provenance
        if isinstance(request.provenance, Provenance)
        else Provenance.model_validate(request.provenance)
    )

    outcomes: list[PhotoSegmentationOutcome] = []
    all_predictions: list[PartPrediction] = []
    all_qualities: list[ImageQuality] = []
    all_artifacts: list[PartArtifact] = []
    batch_reasons: list[Reason] = []

    for photo_input in request.photos:
        photo_id = photo_input.file_id
        # Verify SHA-256
        actual_sha = hashlib.sha256(photo_input.data).hexdigest()
        if actual_sha != photo_input.sha256:
            r = Reason(code="artifact_hash_mismatch", message=f"Hash mismatch for {photo_id}: expected {photo_input.sha256}, got {actual_sha}")
            outcomes.append(PhotoSegmentationOutcome(
                photo_id=photo_id,
                status="failed",
                reasons=(r,),
                predictions=(),
                quality=None,
                mask_ref=None,
                transform=None,
                artifacts=(),
            ))
            batch_reasons.append(r)
            continue

        # Decode photo
        try:
            oriented_img, orientation, (sw, sh) = decode_oriented(photo_input.data)
        except Exception as exc:
            r = Reason(code="corrupt_photo", message=f"Failed to decode photo {photo_id}: {exc}")
            outcomes.append(PhotoSegmentationOutcome(
                photo_id=photo_id,
                status="failed",
                reasons=(r,),
                predictions=(),
                quality=None,
                mask_ref=None,
                transform=None,
                artifacts=(),
            ))
            batch_reasons.append(r)
            continue

        # Transform and letterbox
        transform_spec = plan_model_frame(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            frame_size=cfg.input_size,
            policy=cfg.resize_policy,
        )
        letterboxed_img = letterbox(oriented_img, transform_spec, pad_value=0)
        img_float = letterboxed_img.astype(np.float32) / 255.0
        normalized = (img_float - IMAGENET_MEAN) / IMAGENET_STD
        tensor = torch.from_numpy(normalized).permute(2, 0, 1).unsqueeze(0).float().to(segmenter.device)

        # Inference
        is_cuda = segmenter.device.type == "cuda"
        with torch.no_grad():
            with torch.amp.autocast(device_type="cuda" if is_cuda else "cpu", enabled=is_cuda):
                outputs = segmenter.model(pixel_values=tensor)
                logits = outputs.logits
            upsampled = F.interpolate(
                logits,
                size=(cfg.input_size, cfg.input_size),
                mode="bilinear",
                align_corners=False,
            )
            probs = F.softmax(upsampled, dim=1)[0]
            preds = probs.argmax(dim=0).cpu().numpy().astype(np.uint8)

        # Paletted mask PNG
        mask_pil = Image.fromarray(preds, mode="P")
        mask_pil.putpalette(build_palette())
        buf = io.BytesIO()
        mask_pil.save(buf, format="PNG")
        mask_bytes = buf.getvalue()
        mask_sha256 = hashlib.sha256(mask_bytes).hexdigest()

        v_sig = version_signature(request.versions)
        mask_key = f"claims/{request.claim_id}/{request.input_revision}/parts/{photo_id}/mask.png"
        mask_uri = f"{request.object_uri_prefix}{mask_key}"

        mask_ref = MaskRef(
            artifact_id=deterministic_id("pm", request.job_key, photo_id),
            object_uri=mask_uri,
            sha256=mask_sha256,
            width=cfg.input_size,
            height=cfg.input_size,
            encoding="class_index_png",
            source_photo_id=photo_id,
        )

        mask_artifact = PartArtifact(
            artifact_id=mask_ref.artifact_id,
            key=mask_key,
            object_uri=mask_uri,
            media_type="image/png",
            sha256=mask_sha256,
            byte_count=len(mask_bytes),
            data=mask_bytes,
        )

        photo_artifacts = [mask_artifact]

        # Transform
        image_transform = ImageTransform(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            model_width=cfg.input_size,
            model_height=cfg.input_size,
            scale=transform_spec.scale,
            pad_left=float(transform_spec.pad_x),
            pad_top=float(transform_spec.pad_y),
            mask_frame="model",
        )

        # Overlay artifact if requested
        if cfg.write_overlay:
            try:
                overlay_bytes = render_photo_overlay(
                    photo_image=photo_input.data,
                    photo_id=photo_id,
                    part_masks={"parts": mask_bytes},
                    transform=image_transform,
                    apply_exif=True,
                    show_damage=False,
                    show_parts=True,
                )
                overlay_key = f"claims/{request.claim_id}/{request.input_revision}/parts/{photo_id}/overlay.png"
                overlay_uri = f"{request.object_uri_prefix}{overlay_key}"
                overlay_artifact = PartArtifact(
                    artifact_id=deterministic_id("art", request.job_key, photo_id, "overlay"),
                    key=overlay_key,
                    object_uri=overlay_uri,
                    media_type="image/png",
                    sha256=hashlib.sha256(overlay_bytes).hexdigest(),
                    byte_count=len(overlay_bytes),
                    data=overlay_bytes,
                )
                photo_artifacts.append(overlay_artifact)
            except Exception as exc:
                log.warning(f"Could not build overlay for {photo_id}: {exc}")

        # PartPredictions
        predictions: list[PartPrediction] = []
        for class_id, part_code in segmenter.id_to_part_code.items():
            if class_id == 0 or part_code == "background":
                continue
            class_mask = (preds == class_id)
            pixel_count = int(class_mask.sum())
            if pixel_count <= 0:
                continue

            mean_conf = float(probs[class_id, class_mask].mean().item())
            accepted = (pixel_count >= cfg.min_part_pixels) and (mean_conf >= cfg.min_part_confidence)
            pred = PartPrediction(
                prediction_id=deterministic_id("pp", request.job_key, part_code),
                photo_id=photo_id,
                part_code=part_code,
                side="unknown",
                mask_ref=mask_ref,
                mean_confidence=round(mean_conf, 4),
                pixel_count=pixel_count,
                accepted=accepted,
                transform=image_transform,
                claim_id=request.claim_id,
                input_revision=request.input_revision,
                versions=dict(request.versions),
                provenance=prov,
            )
            predictions.append(pred)

        # ImageQuality
        q_state: Literal["acceptable", "limited", "unusable", "not_assessed"] = "not_assessed"
        q_reasons: list[str] = ["quality_not_assessed"]
        if sw < 200 or sh < 200:
            q_state = "limited"
            q_reasons = ["resolution_low"]

        quality = ImageQuality(
            claim_id=request.claim_id,
            input_revision=request.input_revision,
            photo_id=photo_id,
            state=q_state,
            blur_score=None,
            exposure_state="not_assessed",
            reasons=q_reasons,
            config_version=cfg.config_version,
            versions=dict(request.versions),
            provenance=prov,
        )

        outcome = PhotoSegmentationOutcome(
            photo_id=photo_id,
            status="succeeded",
            reasons=(),
            predictions=tuple(predictions),
            quality=quality,
            mask_ref=mask_ref,
            transform=image_transform,
            artifacts=tuple(photo_artifacts),
        )
        outcomes.append(outcome)
        all_predictions.extend(predictions)
        all_qualities.append(quality)
        all_artifacts.extend(photo_artifacts)

    # Determine batch processing status
    failed_count = sum(1 for o in outcomes if o.status == "failed")
    if not outcomes or failed_count == len(outcomes):
        overall_status: ProcessingStatus = "failed"
    elif failed_count > 0:
        overall_status = "partial"
    else:
        overall_status = "succeeded"

    elapsed_ms = round((clock() - started) * 1000.0, 1)

    return PartsSegmentResult(
        processing_status=overall_status,
        reasons=tuple(batch_reasons),
        photo_outcomes=tuple(outcomes),
        predictions=tuple(all_predictions),
        qualities=tuple(all_qualities),
        artifacts=tuple(all_artifacts),
        metrics={"total_ms": elapsed_ms, "photos_processed": len(outcomes), "photos_failed": failed_count},
        retryable=any(r.code in ("artifact_hash_mismatch",) for r in batch_reasons),
    )


def make_parts_handler(segmenter: PartsSegmenter, storage: Storage):
    """Factory creating a ConsumerRuntime handler for cmev.cmd.parts-segment.v1."""

    def handler(ctx: Context) -> dict[str, Any]:
        payload = ctx.payload
        photo_ref = payload["photo"]
        photo_id = photo_ref["file_id"]

        # Fetch image bytes from storage
        object_uri = photo_ref.get("object_uri", "")
        if object_uri.startswith("s3://"):
            key = object_uri.split("/", 3)[-1]
        elif object_uri.startswith("file://local-evidence/"):
            key = object_uri.replace("file://local-evidence/", "")
        else:
            key = object_uri

        try:
            photo_bytes = storage.read(key)
        except Exception as exc:
            raise PermanentError("artifact_read_failed", f"Failed to read photo bytes for {photo_id}: {exc}") from exc

        # Verify sha256 hash if provided
        expected_sha = photo_ref.get("sha256")
        if expected_sha:
            actual_sha = hashlib.sha256(photo_bytes).hexdigest()
            if actual_sha != expected_sha:
                raise PermanentError("artifact_hash_mismatch", f"Hash mismatch for {photo_id}: expected {expected_sha}, got {actual_sha}")

        # Run segmentation
        predictions, quality, mask_ref, transform = segmenter.segment_photo(
            photo_bytes=photo_bytes,
            photo_id=photo_id,
            claim_id=ctx.envelope.claim_id,
            input_revision=ctx.envelope.input_revision,
            job_key=ctx.envelope.job_key,
            versions=ctx.envelope.versions,
            provenance=ctx.provenance(producer_service="cmev-worker-parts"),
            storage=storage,
        )

        # Persist rows to database
        records: list[tuple[str, Any]] = [("image_quality", quality)]
        for p in predictions:
            records.append(("part_prediction", p))

        insert_records(
            ctx.session,
            records,
            claim_id=ctx.envelope.claim_id,
            input_revision=ctx.envelope.input_revision,
            stage="parts",
            job_key=ctx.envelope.job_key,
            now=ctx.now,
        )

        # Build part_mask_ref matching envelope.v1.schema.json#/$defs/maskRef strictly
        # (NO extra properties like source_photo_id)
        part_mask_ref_event: dict[str, Any] = {
            "artifact_id": mask_ref.artifact_id,
            "object_uri": mask_ref.object_uri,
            "sha256": mask_ref.sha256,
            "width": mask_ref.width,
            "height": mask_ref.height,
            "encoding": mask_ref.encoding,
        }
        if mask_ref.component_index is not None:
            part_mask_ref_event["component_index"] = mask_ref.component_index

        accepted_parts = [p for p in predictions if p.accepted]

        event_payload = {
            "photo_id": photo_id,
            "part_mask_ref": part_mask_ref_event,
            "transform": transform.model_dump(mode="json"),
            "parts": [
                {
                    "part_code": p.part_code,
                    "pixel_count": p.pixel_count,
                    "mean_confidence": p.mean_confidence,
                }
                for p in accepted_parts
            ],
            "part_prediction_ids": [p.prediction_id for p in predictions],
            "image_quality": {
                "state": quality.state,
                "blur_score": quality.blur_score,
                "exposure_state": quality.exposure_state,
                "reasons": list(quality.reasons),
            },
            "empty_result": len(accepted_parts) == 0,
        }

        from claim_cmev.contracts.events import Envelope

        topic = "cmev.evt.parts-segmented.v1"
        out_envelope = Envelope.build(
            topic=topic,
            claim_id=ctx.envelope.claim_id,
            input_revision=ctx.envelope.input_revision,
            task="parts_segment",
            versions=ctx.envelope.versions,
            provenance=ctx.provenance(producer_service="cmev-worker-parts"),
            trace_id=ctx.envelope.trace_id,
            occurred_at=ctx.now,
            target=photo_id,
        )
        ctx.emit(topic, out_envelope.message(event_payload), job_key=ctx.envelope.job_key)

        return {
            "photo_id": photo_id,
            "part_prediction_ids": event_payload["part_prediction_ids"],
            "part_mask_ref": event_payload["part_mask_ref"],
            "transform": event_payload["transform"],
            "accepted_parts_count": len(accepted_parts),
        }

    return handler
