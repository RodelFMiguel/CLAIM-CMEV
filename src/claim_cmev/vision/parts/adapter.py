"""Online serving adapter for M1 Vehicle Part Segmentation.

Loads the trained SegFormer-B0 checkpoint, decodes vehicle photographs with EXIF
orientation, prepares the 512x512 model frame, runs inference, saves paletted mask PNGs
to object storage, and produces PartPrediction and ImageQuality records conforming
to CLAIM-CMEV data and integration contracts.

Specification: docs/specs/module-01-vehicle-part-segmentation.md
               docs/specs/integration_contracts.md section 5.4
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
from transformers import SegformerConfig, SegformerForSemanticSegmentation

from claim_cmev.contracts.common import (
    Provenance,
    Versions,
    deterministic_id,
)
from claim_cmev.contracts.imaging import (
    ImageQuality,
    ImageTransform,
    MaskRef,
    PartPrediction,
)
from claim_cmev.messaging.consumer import Context, PermanentError
from claim_cmev.persistence.store import insert_records
from claim_cmev.storage import Storage
from claim_cmev.vision.transforms import decode_oriented, letterbox, plan_model_frame

from .config import PartsConfig, load_parts_config

log = logging.getLogger("cmev.vision.parts.adapter")

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


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

        if device_str:
            self.device = torch.device(device_str)
        elif self.config.device in ("cuda", "cpu"):
            self.device = torch.device(self.config.device)
        else:
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

        # 5. Run inference with mixed precision
        with torch.no_grad():
            with torch.amp.autocast(device_type="cuda" if self.device.type == "cuda" else "cpu", enabled=(self.device.type == "cuda")):
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
        from pipelines.vision.convert_hitl import build_palette
        mask_pil = Image.fromarray(preds, mode="P")
        mask_pil.putpalette(build_palette())

        buf = io.BytesIO()
        mask_pil.save(buf, format="PNG")
        mask_bytes = buf.getvalue()
        mask_sha256 = hashlib.sha256(mask_bytes).hexdigest()

        mask_key = f"claims/{claim_id}/{input_revision}/parts/{photo_id}/mask.png"
        storage.write(mask_key, mask_bytes, "image/png")
        mask_uri = storage.uri(mask_key)

        mask_ref = MaskRef(
            artifact_id=deterministic_id("pm", job_key, photo_id),
            object_uri=mask_uri,
            sha256=mask_sha256,
            width=self.config.input_size,
            height=self.config.input_size,
            encoding="indexed_png",
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

        # 8. Build PartPrediction rows
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

        # 9. Simple image quality screening
        quality_state = "acceptable"
        reasons: list[str] = []
        if sw < 200 or sh < 200:
            quality_state = "limited"
            reasons.append("resolution_low")

        quality = ImageQuality(
            claim_id=claim_id,
            input_revision=input_revision,
            photo_id=photo_id,
            state=quality_state,
            blur_score=None,
            exposure_state="normal",
            reasons=reasons,
            config_version=self.config.config_version,
            versions=dict(versions),
            provenance=Provenance.model_validate(provenance),
        )

        return predictions, quality, mask_ref, image_transform


def make_parts_handler(segmenter: PartsSegmenter, storage: Storage):
    """Factory creating a ConsumerRuntime handler for cmev.cmd.parts-segment.v1."""

    def handler(ctx: Context) -> dict[str, Any]:
        payload = ctx.payload
        photo_ref = payload["photo"]
        photo_id = photo_ref["file_id"]

        # Fetch image bytes from storage
        object_uri = photo_ref.get("object_uri", "")
        # Extract object key from URI
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

        # Build and emit cmev.evt.parts-segmented.v1
        event_payload = {
            "photo_id": photo_id,
            "part_mask_ref": mask_ref.model_dump(mode="json"),
            "transform": transform.model_dump(mode="json"),
            "parts": [
                {
                    "part_code": p.part_code,
                    "pixel_count": p.pixel_count,
                    "mean_confidence": p.mean_confidence,
                }
                for p in predictions
                if p.accepted
            ],
            "part_prediction_ids": [p.prediction_id for p in predictions],
            "image_quality": {
                "state": quality.state,
                "blur_score": quality.blur_score,
                "exposure_state": quality.exposure_state,
                "reasons": list(quality.reasons),
            },
            "empty_result": len(predictions) == 0,
        }

        from claim_cmev.contracts.events import Envelope
        from claim_cmev.contracts.events.envelope import compute_dedup_key

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
            "accepted_parts_count": len(event_payload["parts"]),
        }

    return handler
