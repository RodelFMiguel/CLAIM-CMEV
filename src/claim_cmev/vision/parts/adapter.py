"""Online serving adapter for M1 Vehicle Part Segmentation.

Loads the trained SegFormer checkpoint, decodes vehicle photographs with EXIF
orientation, prepares the 512x512 model frame, runs inference, and produces the paletted
mask PNG, the display overlay, and PartPrediction and ImageQuality records conforming to
CLAIM-CMEV data and integration contracts.

``run_parts_segmentation`` is the one entry point and writes nothing. The consumer handler
stores its artifacts, persists its rows and publishes the completion event.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/integration_contracts.md section 5.4
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import io
import json
import logging
from pathlib import Path
import time
from typing import Any, Literal

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
import transformers
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
from claim_cmev.messaging.consumer import Context, PermanentError, TransientError
from claim_cmev.messaging.outbox import build_message
from claim_cmev.persistence.store import insert_records
from claim_cmev.review.overlays import render_photo_overlay
from claim_cmev.storage import Storage
from claim_cmev.vision.palette import ID_TO_PART_CODE, build_palette
from claim_cmev.vision.transforms import decode_oriented, letterbox, plan_model_frame

from .config import PartsConfig, load_parts_config

log = logging.getLogger("cmev.vision.parts.adapter")

PIXEL_MEAN = (0.485, 0.456, 0.406)
PIXEL_STD = (0.229, 0.224, 0.225)
PAD_VALUE = 0
IMAGENET_MEAN = np.array(PIXEL_MEAN, dtype=np.float32)
IMAGENET_STD = np.array(PIXEL_STD, dtype=np.float32)

ProcessingStatus = Literal["succeeded", "partial", "failed"]
PARTS_EVENT_TOPIC = "cmev.evt.parts-segmented.v1"
RETRYABLE_REASONS: frozenset[str] = frozenset()
"""Per-photo failures worth another attempt. There are none: a hash mismatch and an
undecodable photo fail the same way every time (module-01: a mismatch "is not silently re-fetched")."""


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
    """Batch result of M1 segmentation conforming to module specification.

    ``artifacts`` carry their bytes; the caller stores them at each artifact's ``key``.
    """

    processing_status: ProcessingStatus
    reasons: tuple[Reason, ...]
    photo_outcomes: tuple[PhotoSegmentationOutcome, ...]
    predictions: tuple[PartPrediction, ...]
    qualities: tuple[ImageQuality, ...]
    artifacts: tuple[PartArtifact, ...]
    metrics: dict[str, Any]
    retryable: bool


WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


class ModelUnavailable(RuntimeError):
    """The configured checkpoint cannot be served, so the worker must refuse to start."""

    def __init__(self, reason_code: str, reason_text: str):
        super().__init__(f"{reason_code}: {reason_text}")
        self.reason_code, self.reason_text = reason_code, reason_text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def serving_preprocessing(config: PartsConfig) -> dict[str, Any]:
    """The model frame this worker builds, in the vocabulary of a registry entry's ``preprocessing.json``."""
    return {"input_size": config.input_size, "resize_policy": config.resize_policy, "pixel_mean": list(PIXEL_MEAN),
            "pixel_std": list(PIXEL_STD), "pad_value": PAD_VALUE}


def _same_setting(recorded: Any, served: Any) -> bool:
    if isinstance(served, list):
        return (isinstance(recorded, list) and len(recorded) == len(served)
                and all(isinstance(value, (int, float)) and abs(value - expected) < 1e-6
                        for value, expected in zip(recorded, served)))
    return recorded == served


def verify_checkpoint(model_dir: str | Path, config: PartsConfig) -> dict[str, Any]:
    """Check a registry entry against its manifest and the configuration; returns the manifest.

    Technical specification 9.3: a worker verifies the weight file's SHA-256 against its
    manifest at start and refuses to start on a mismatch. Another model version, taxonomy
    version or class numbering is refused as well, never remapped. So is a model trained in
    another frame than the one this worker builds: its recorded scores would not describe
    what is served.
    """
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise ModelUnavailable("model_not_found", f"no registry entry at {model_dir}")
    manifest_path = model_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ModelUnavailable("model_manifest_missing", f"{manifest_path} does not exist")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != config.model_version:
        raise ModelUnavailable("model_version_mismatch", f"the manifest names {manifest.get('version')!r}, "
                               f"the configuration {config.model_version!r}")
    if manifest.get("taxonomy_version") != config.taxonomy_version:
        raise ModelUnavailable("taxonomy_version_mismatch", f"the manifest names {manifest.get('taxonomy_version')!r}, "
                               f"the configuration {config.taxonomy_version!r}")
    weights = next((model_dir / name for name in WEIGHT_FILES if (model_dir / name).is_file()), None)
    if weights is None:
        raise ModelUnavailable("model_weights_missing", f"no weight file in {model_dir}")
    if _sha256_file(weights) != manifest.get("weights_sha256"):
        raise ModelUnavailable("model_weights_hash_mismatch", f"{weights.name} does not match its manifest hash")
    labels = json.loads((model_dir / "config.json").read_text(encoding="utf-8")).get("id2label") or {}
    if {int(class_id): code for class_id, code in labels.items()} != ID_TO_PART_CODE:
        raise ModelUnavailable("label_map_mismatch", "the checkpoint's class map is not the parts taxonomy's")
    preprocessing_path = model_dir / "preprocessing.json"
    if not preprocessing_path.is_file():
        raise ModelUnavailable("preprocessing_missing", f"{preprocessing_path} does not exist, so the frame the "
                               "model was trained in is not recorded")
    trained = json.loads(preprocessing_path.read_text(encoding="utf-8"))
    served = serving_preprocessing(config)
    differing = [key for key, value in served.items() if not _same_setting(trained.get(key), value)]
    if differing:
        raise ModelUnavailable("preprocessing_mismatch", "; ".join(
            f"{key}: trained with {trained.get(key)!r}, served with {served[key]!r}" for key in differing))
    return manifest


class PartsSegmenter:
    """Inference engine for M1 vehicle part segmentation."""

    def __init__(
        self,
        model_dir: str | Path,
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
        model, loading = SegformerForSemanticSegmentation.from_pretrained(
            self.model_dir,
            config=self.seg_config,
            output_loading_info=True,
        )
        # from_pretrained only warns when tensor names differ, for example across transformers
        # versions, and leaves those layers at random values. Such a model must not be served.
        unloaded = sorted([*loading.get("missing_keys", ()), *loading.get("unexpected_keys", ()),
                           *(entry[0] for entry in loading.get("mismatched_keys", ()))])
        if unloaded:
            saved_with = getattr(self.seg_config, "transformers_version", None) or "an unrecorded version"
            raise ModelUnavailable(
                "model_weights_incomplete",
                f"{len(unloaded)} tensors of {self.model_dir} did not load (for example {unloaded[0]}). The checkpoint "
                f"was saved with transformers {saved_with}; transformers {transformers.__version__} is installed")
        self.model = model.to(self.device)
        self.model.eval()

        # Build ID to code mapping from model config
        self.id_to_part_code = {int(k): v for k, v in self.seg_config.id2label.items()}


def load_parts_segmenter(config: PartsConfig | None = None,
                         registry_root: str | Path | None = None) -> PartsSegmenter:
    """Load the configured registry entry after verifying it; raises ``ModelUnavailable`` otherwise."""
    config = config or load_parts_config()
    model_dir = config.model_dir(registry_root)
    verify_checkpoint(model_dir, config)
    return PartsSegmenter(model_dir=model_dir, config=config)


def render_parts_overlay(photo: bytes, photo_id: str, class_mask: np.ndarray, part_ids: Iterable[int],
                         transform: ImageTransform) -> bytes:
    """PNG of the photograph with one outline per listed part class.

    ``class_mask`` is the model-frame class-index mask. Each listed part is outlined on its
    own, so the seam between two neighbouring parts is drawn; other classes get no outline.
    """
    masks = {ID_TO_PART_CODE[part_id]: Image.fromarray(np.where(class_mask == part_id, 255, 0).astype(np.uint8))
             for part_id in part_ids}
    return render_photo_overlay(photo_image=photo, photo_id=photo_id, part_masks=masks, transform=transform,
                                apply_exif=True, show_damage=False, show_parts=True)


def _failed(photo_id: str, reason: Reason) -> PhotoSegmentationOutcome:
    return PhotoSegmentationOutcome(photo_id=photo_id, status="failed", reasons=(reason,), predictions=(),
                                    quality=None, mask_ref=None, transform=None, artifacts=())


def _artifact(artifact_id: str, key: str, uri_prefix: str, data: bytes) -> PartArtifact:
    return PartArtifact(artifact_id=artifact_id, key=key, object_uri=f"{uri_prefix}{key}", media_type="image/png",
                        sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data), data=data)


def run_parts_segmentation(
    request: PartsSegmentRequest,
    segmenter: PartsSegmenter,
    config: PartsConfig | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> PartsSegmentResult:
    """Adapter entry point: segment each photo of ``request`` and return its records and artifacts.

    Nothing is written here. The caller stores the returned artifacts, persists the rows
    and publishes the event (module-01, "Adapter entry point").
    """
    started = clock()
    cfg = config or segmenter.config
    prov = (
        request.provenance
        if isinstance(request.provenance, Provenance)
        else Provenance.model_validate(request.provenance)
    )
    versions = dict(request.versions)
    v_sig = version_signature(versions)

    outcomes: list[PhotoSegmentationOutcome] = []
    batch_reasons: list[Reason] = []

    for photo_input in request.photos:
        photo_id = photo_input.file_id
        # Verify SHA-256
        actual_sha = hashlib.sha256(photo_input.data).hexdigest()
        if actual_sha != photo_input.sha256:
            r = Reason(code="artifact_hash_mismatch", message=f"Hash mismatch for {photo_id}: expected {photo_input.sha256}, got {actual_sha}")
            outcomes.append(_failed(photo_id, r))
            batch_reasons.append(r)
            continue

        # Decode photo
        try:
            oriented_img, orientation, (sw, sh) = decode_oriented(photo_input.data)
        except Exception as exc:
            r = Reason(code="corrupt_photo", message=f"Failed to decode photo {photo_id}: {exc}")
            outcomes.append(_failed(photo_id, r))
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
        letterboxed_img = letterbox(oriented_img, transform_spec, pad_value=PAD_VALUE)
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

        # The padding holds no photograph: it is background in the mask, so no part is counted there.
        x0, y0, x1, y1 = transform_spec.content_box()
        padding = np.ones(preds.shape, dtype=bool)
        padding[y0:y1, x0:x1] = False
        preds[padding] = 0

        # Paletted mask PNG, stored under the version signature so a rerun with another
        # checkpoint never overwrites the mask that earlier rows point at.
        mask_pil = Image.fromarray(preds, mode="P")
        mask_pil.putpalette(build_palette())
        buf = io.BytesIO()
        mask_pil.save(buf, format="PNG")
        folder = f"claims/{request.claim_id}/{request.input_revision}/parts/{photo_id}/{v_sig}"
        mask_artifact = _artifact(deterministic_id("pm", request.job_key, photo_id), f"{folder}/mask.png",
                                  request.object_uri_prefix, buf.getvalue())
        mask_ref = MaskRef(
            artifact_id=mask_artifact.artifact_id,
            object_uri=mask_artifact.object_uri,
            sha256=mask_artifact.sha256,
            width=cfg.input_size,
            height=cfg.input_size,
            encoding="class_index_png",
            source_photo_id=photo_id,
        )
        photo_artifacts = [mask_artifact]

        # Transform. pad_left/pad_top are the photo's offset in the model frame, not the total padding.
        image_transform = ImageTransform(
            stored_width=sw,
            stored_height=sh,
            exif_orientation=orientation,
            model_width=cfg.input_size,
            model_height=cfg.input_size,
            scale=transform_spec.scale,
            pad_left=float(transform_spec.offset_x),
            pad_top=float(transform_spec.offset_y),
            mask_frame="model",
        )

        # PartPredictions
        predictions: list[PartPrediction] = []
        accepted_ids: list[int] = []
        for class_id, part_code in segmenter.id_to_part_code.items():
            if class_id == 0 or part_code == "background":
                continue
            class_mask = (preds == class_id)
            pixel_count = int(class_mask.sum())
            if pixel_count <= 0:
                continue

            mean_conf = float(probs[class_id, class_mask].mean().item())
            accepted = (pixel_count >= cfg.min_part_pixels) and (mean_conf >= cfg.min_part_confidence)
            if accepted:
                accepted_ids.append(class_id)
            pred = PartPrediction(
                prediction_id=deterministic_id("pp", request.job_key, photo_id, part_code),
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
                versions=versions,
                provenance=prov,
            )
            predictions.append(pred)

        # Overlay artifact if requested: one outline per accepted part, none when nothing was accepted
        if cfg.write_overlay and accepted_ids:
            try:
                overlay_bytes = render_parts_overlay(photo_input.data, photo_id, preds, accepted_ids, image_transform)
                photo_artifacts.append(_artifact(deterministic_id("art", request.job_key, photo_id, "overlay"),
                                                 f"{folder}/overlay.png", request.object_uri_prefix, overlay_bytes))
            except Exception as exc:
                log.warning(f"Could not build overlay for {photo_id}: {exc}")

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
            versions=versions,
            provenance=prov,
        )

        outcomes.append(PhotoSegmentationOutcome(
            photo_id=photo_id,
            status="succeeded",
            reasons=(),
            predictions=tuple(predictions),
            quality=quality,
            mask_ref=mask_ref,
            transform=image_transform,
            artifacts=tuple(photo_artifacts),
        ))

    # Determine batch processing status
    succeeded = [o for o in outcomes if o.status == "succeeded"]
    failed_count = len(outcomes) - len(succeeded)
    if not succeeded:
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
        predictions=tuple(p for o in succeeded for p in o.predictions),
        qualities=tuple(o.quality for o in succeeded),
        artifacts=tuple(a for o in succeeded for a in o.artifacts),
        metrics={"total_ms": elapsed_ms, "photos_processed": len(outcomes), "photos_failed": failed_count},
        retryable=any(r.code in RETRYABLE_REASONS for r in batch_reasons),
    )


def _storage_key(object_uri: str) -> str:
    if object_uri.startswith("s3://"):
        return object_uri.split("/", 3)[-1]
    return object_uri.removeprefix("file://local-evidence/")


def _read_photo(storage: Storage, object_uri: str, photo_id: str) -> bytes:
    """Photo bytes by URI. An absent object is final; any other storage failure is worth a retry."""
    try:
        return storage.read(_storage_key(object_uri))
    except Exception as exc:  # noqa: BLE001 - classified below
        response = getattr(exc, "response", None)  # botocore's ClientError carries the S3 error code here
        s3_code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        if isinstance(exc, FileNotFoundError) or s3_code in ("NoSuchKey", "404", "NotFound"):
            raise PermanentError("artifact_missing", f"photo {photo_id} is not in the object store") from exc
        raise TransientError("artifact_read_failed",
                             f"could not read photo {photo_id}: {type(exc).__name__}") from exc


def make_parts_handler(segmenter: PartsSegmenter, storage: Storage, versions: Mapping[str, str]):
    """Factory creating a ConsumerRuntime handler for cmev.cmd.parts-segment.v1.

    ``versions`` are the stage versions of the checkpoint ``segmenter`` loaded. A command
    pinned to anything else, such as the fixture tags, is refused, so no row is recorded
    under a version that did not produce it.
    """
    served = dict(versions)

    def handler(ctx: Context) -> dict[str, Any]:
        env = ctx.envelope
        if dict(env.versions) != served:
            raise PermanentError("model_version_unsupported",
                                 "this worker serves only commands pinned to the versions it loaded")
        photo_ref = ctx.payload["photo"]
        photo_id = photo_ref["file_id"]
        photo_bytes = _read_photo(storage, photo_ref["object_uri"], photo_id)

        # The wire contract carries one photograph per command.
        request = PartsSegmentRequest(
            claim_id=env.claim_id,
            input_revision=env.input_revision,
            job_key=env.job_key,
            photos=(PhotoFileInput(file_id=photo_id, media_type=photo_ref["media_type"],
                                   sha256=photo_ref["sha256"], data=photo_bytes),),
            versions=env.versions,
            provenance=ctx.provenance(),
            object_uri_prefix=storage.uri(""),
        )
        outcome = run_parts_segmentation(request, segmenter).photo_outcomes[0]
        if outcome.status == "failed":
            reason = outcome.reasons[0]
            raise PermanentError(reason.code, reason.message)

        for artifact in outcome.artifacts:
            storage.write(artifact.key, artifact.data, artifact.media_type)

        # Persist rows to database
        records: list[tuple[str, Any]] = [("image_quality", outcome.quality)]
        for p in outcome.predictions:
            records.append(("part_prediction", p))

        insert_records(
            ctx.session,
            records,
            claim_id=env.claim_id,
            input_revision=env.input_revision,
            stage="parts",
            job_key=env.job_key,
            now=ctx.now,
        )

        # part_mask_ref matching envelope.v1.schema.json#/$defs/maskRef strictly
        # (NO extra properties like source_photo_id)
        mask_ref = outcome.mask_ref
        part_mask_ref_event: dict[str, Any] = {
            "artifact_id": mask_ref.artifact_id,
            "object_uri": mask_ref.object_uri,
            "sha256": mask_ref.sha256,
            "width": mask_ref.width,
            "height": mask_ref.height,
            "encoding": mask_ref.encoding,
        }
        accepted_parts = [p for p in outcome.predictions if p.accepted]
        quality = outcome.quality

        event_payload = {
            "photo_id": photo_id,
            "part_mask_ref": part_mask_ref_event,
            "transform": outcome.transform.model_dump(mode="json"),
            "parts": [
                {
                    "part_code": p.part_code,
                    "pixel_count": p.pixel_count,
                    "mean_confidence": p.mean_confidence,
                }
                for p in accepted_parts
            ],
            "part_prediction_ids": [p.prediction_id for p in outcome.predictions],
            "image_quality": {
                "state": quality.state,
                "blur_score": quality.blur_score,
                "exposure_state": quality.exposure_state,
                "reasons": list(quality.reasons),
            },
            "empty_result": len(accepted_parts) == 0,
        }

        # The same envelope every producer builds: this job's task, target and retry epoch,
        # caused by the command.
        job = ctx.job or {}
        message = build_message(
            PARTS_EVENT_TOPIC, claim_id=env.claim_id, input_revision=env.input_revision, task=job["task"],
            versions=env.versions, target=job["target"], attempt_epoch=job["attempt_epoch"], trace_id=env.trace_id,
            occurred_at=ctx.now, payload=event_payload, causation_id=env.dedup_key, provenance=ctx.provenance())
        ctx.emit(PARTS_EVENT_TOPIC, message)

        return {
            "photo_id": photo_id,
            "part_prediction_ids": event_payload["part_prediction_ids"],
            "part_mask_ref": event_payload["part_mask_ref"],
            "transform": event_payload["transform"],
            "accepted_parts_count": len(accepted_parts),
        }

    return handler
