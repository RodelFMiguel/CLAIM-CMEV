"""M2 serving: the damage checkpoint, the adapter entry point and the Kafka handler.

Specification: docs/specs/module-02-damage-segmentation.md

``run_damage_segmentation`` builds the same model frame as M1, runs the damage model and hands
the class mask to ``assign_damage_to_part`` with the M1 part mask of that photograph. It writes
nothing: the handler stores the artifacts, persists the observations and publishes the event.

The served checkpoint decides the damage vocabulary. Its registry entry names the taxonomy and
the class numbering; observations carry that taxonomy in ``versions``. Side is always unknown.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import io
import logging
from pathlib import Path
import time
from typing import Any

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

from ...contracts.common import ContractError, Provenance, deterministic_id, version_signature
from ...contracts.imaging import ImageDamageObservation, ImageTransform, MaskRef
from ...messaging.consumer import Context, PermanentError, TransientError
from ...messaging.outbox import build_message
from ...persistence.store import insert_records
from ..artifacts import is_missing, read_object, storage_key
from ..frame import build_model_frame, same_frame
from ..palette import ID_TO_PART_CODE, build_palette
from ..registry import ModelUnavailable, class_map, serving_preprocessing, verify_registry_entry
from ..segformer import load_strict, resolve_device
from .assignment import DamageAssignmentResult, ObservationContext, assign_damage_to_part
from .config import AssignmentConfig, load_assignment_config
from .model_config import DamageModelConfig, load_damage_model_config

log = logging.getLogger("cmev.vision.damage.adapter")

DAMAGE_EVENT_TOPIC = "cmev.evt.damage-segmented.v1"
BACKGROUND = "background"
PART_CLASSES = {class_id: code for class_id, code in ID_TO_PART_CODE.items() if class_id != 0}


class DamageInputError(Exception):
    """An input of one damage job is not usable. The same input fails the same way every time."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"{reason_code}: {message}")
        self.reason_code, self.message = reason_code, message


# ---------------------------------------------------------------------------
# The checkpoint
# ---------------------------------------------------------------------------

def verify_damage_checkpoint(model_dir: str | Path, config: DamageModelConfig) -> dict[str, Any]:
    """Check a registry entry against its manifest and the configuration; returns the manifest.

    Besides the shared checks (``claim_cmev.vision.registry``), the entry's classes must be
    background at 0 and, in any order, exactly the codes of the configured damage taxonomy.
    The order is the checkpoint's own and is never renumbered.
    """
    codes = set(config.damage_codes)

    def labels_match(labels: dict[int, str]) -> bool:
        return (sorted(labels) == list(range(len(codes) + 1)) and labels[0] == BACKGROUND
                and {code for class_id, code in labels.items() if class_id} == codes)

    return verify_registry_entry(
        model_dir, model_version=config.model_version, taxonomy_version=config.taxonomy_version,
        preprocessing=serving_preprocessing(config.input_size, config.resize_policy), labels_match=labels_match,
        taxonomy_name="damage taxonomy")


class DamageSegmenter:
    """The loaded damage model and the class numbering of its registry entry."""

    def __init__(self, model_dir: str | Path, config: DamageModelConfig, device_str: str | None = None) -> None:
        self.model_dir, self.config = Path(model_dir), config
        self.device = resolve_device(config.device, device_str)
        self.damage_classes = {class_id: code for class_id, code in class_map(self.model_dir).items() if class_id}
        log.info("Loading the damage model from %s to %s", self.model_dir, self.device)
        self.model = load_strict(self.model_dir, self.device)
        if self.model.config.num_labels != len(self.damage_classes) + 1:
            raise ModelUnavailable("label_map_mismatch", f"the checkpoint has {self.model.config.num_labels} "
                                   f"classes, its class map {len(self.damage_classes) + 1}")


def load_damage_segmenter(config: DamageModelConfig | None = None,
                          registry_root: str | Path | None = None) -> DamageSegmenter:
    """Load the configured registry entry after verifying it; raises ``ModelUnavailable`` otherwise."""
    config = config or load_damage_model_config()
    model_dir = config.model_dir(registry_root)
    verify_damage_checkpoint(model_dir, config)
    return DamageSegmenter(model_dir, config)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DamageSegmentRequest:
    """One photograph, the M1 part mask made from it and the frame M1 recorded."""

    claim_id: str
    input_revision: int
    job_key: str
    photo_id: str
    photo: bytes = field(repr=False)
    photo_sha256: str
    part_mask: bytes | None = field(repr=False)
    """The M1 mask PNG, or None when it does not exist: the damage then keeps unknown part identity."""
    part_mask_ref: MaskRef | None
    transform: ImageTransform
    accepted_parts: tuple[str, ...]
    versions: Mapping[str, str]
    provenance: Provenance | Mapping[str, Any]
    object_uri_prefix: str = "s3://cmev-evidence/"


@dataclass(frozen=True)
class DamageArtifact:
    artifact_id: str
    key: str
    object_uri: str
    media_type: str
    sha256: str
    byte_count: int
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class DamageSegmentResult:
    """Observations and artifacts of one photograph. ``artifacts`` carry their bytes; the caller stores them."""

    assignment: DamageAssignmentResult
    artifacts: tuple[DamageArtifact, ...]
    metrics: dict[str, Any]

    @property
    def observations(self) -> tuple[ImageDamageObservation, ...]:
        return self.assignment.observations

    @property
    def damage_mask_ref(self) -> MaskRef:
        return self.assignment.damage_mask_ref

    def artifact(self, name: str) -> DamageArtifact:
        return next(a for a in self.artifacts if a.key.endswith("/" + name))

    def event_payload(self) -> dict[str, Any]:
        return self.assignment.event_payload()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _artifact(artifact_id: str, key: str, uri_prefix: str, data: bytes) -> DamageArtifact:
    return DamageArtifact(artifact_id=artifact_id, key=key, object_uri=f"{uri_prefix}{key}", media_type="image/png",
                          sha256=_sha256(data), byte_count=len(data), data=data)


def _read_part_mask(request: DamageSegmentRequest) -> np.ndarray | None:
    if request.part_mask is None:
        return None
    if request.part_mask_ref is None:
        raise DamageInputError("part_mask_ref_missing", "a supplied part mask needs its artifact reference")
    if _sha256(request.part_mask) != request.part_mask_ref.sha256:
        raise DamageInputError("artifact_hash_mismatch", f"the part mask of {request.photo_id} is not the one "
                               "the command names")
    try:
        mask = np.array(Image.open(io.BytesIO(request.part_mask)))
    except Exception as exc:  # noqa: BLE001 - any decoding failure is the same refusal
        raise DamageInputError("corrupt_part_mask", f"the part mask of {request.photo_id} does not decode") from exc
    if mask.ndim != 2:
        raise DamageInputError("mask_encoding_mismatch", "a part mask is a single-channel class-index image")
    return mask


def run_damage_segmentation(
    request: DamageSegmentRequest,
    segmenter: DamageSegmenter,
    assignment_config: AssignmentConfig | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> DamageSegmentResult:
    """Adapter entry point: segment the damage on one photograph and assign each region to a part.

    Raises ``DamageInputError`` for an input that is not what the command named. Nothing is
    written here (module-02, "Adapter entry point").
    """
    started = clock()
    config = segmenter.config
    rules = assignment_config or load_assignment_config()
    provenance = (request.provenance if isinstance(request.provenance, Provenance)
                  else Provenance.model_validate(request.provenance))
    versions = dict(request.versions)

    if _sha256(request.photo) != request.photo_sha256:
        raise DamageInputError("artifact_hash_mismatch", f"photo {request.photo_id} is not the one the command names")
    try:
        frame = build_model_frame(request.photo, config.input_size, config.resize_policy)
    except Exception as exc:  # noqa: BLE001 - any decoding failure is the same refusal
        raise DamageInputError("corrupt_photo", f"photo {request.photo_id} does not decode: {exc}") from exc
    # The part mask is only comparable with the damage mask on the pixel grid M1 built.
    if not same_frame(frame.transform, request.transform):
        raise DamageInputError("transform_mismatch", f"the part mask of {request.photo_id} was made in another "
                               "frame than this worker builds for the photograph")
    part_mask = _read_part_mask(request)

    tensor = torch.from_numpy(frame.normalised()).unsqueeze(0).to(segmenter.device)
    is_cuda = segmenter.device.type == "cuda"
    with torch.no_grad():
        with torch.amp.autocast(device_type="cuda" if is_cuda else "cpu", enabled=is_cuda):
            logits = segmenter.model(pixel_values=tensor).logits
        upsampled = F.interpolate(logits.float(), size=frame.pixels.shape[:2], mode="bilinear", align_corners=False)
        confidence, classes = F.softmax(upsampled, dim=1)[0].max(dim=0)
    damage_mask = classes.cpu().numpy().astype(np.uint8)
    confidence_map = confidence.cpu().numpy().astype(np.float64)
    # The padding holds no photograph, so nothing in it is damage.
    damage_mask[frame.padding] = 0

    # Stored under the version signature, so a rerun with another checkpoint never overwrites
    # the mask that earlier observations point at.
    folder = f"claims/{request.claim_id}/{request.input_revision}/damage/{request.photo_id}/{version_signature(versions)}"
    mask_image = Image.fromarray(damage_mask, mode="P")
    mask_image.putpalette(build_palette())
    mask_artifact = _artifact(deterministic_id("dm", request.job_key, request.photo_id), f"{folder}/mask.png",
                              request.object_uri_prefix, _png(mask_image))
    height, width = damage_mask.shape
    damage_mask_ref = MaskRef(artifact_id=mask_artifact.artifact_id, object_uri=mask_artifact.object_uri,
                              sha256=mask_artifact.sha256, width=width, height=height, encoding="class_index_png",
                              source_photo_id=request.photo_id)
    context = ObservationContext(
        claim_id=request.claim_id, input_revision=request.input_revision, photo_id=request.photo_id,
        job_key=request.job_key, versions=versions, provenance=provenance, damage_mask_ref=damage_mask_ref,
        part_mask_ref=request.part_mask_ref if part_mask is not None else None)
    try:
        assignment = assign_damage_to_part(
            damage_mask, part_mask, confidence=confidence_map, config=rules, damage_classes=segmenter.damage_classes,
            part_classes=PART_CLASSES, accepted_parts=request.accepted_parts, context=context,
            transform=request.transform)
    except ContractError as exc:
        raise DamageInputError(exc.reason_code, exc.message) from exc

    components = _artifact(deterministic_id("dc", request.job_key, request.photo_id), f"{folder}/components.png",
                           request.object_uri_prefix, _png(Image.fromarray(assignment.component_labels)))
    return DamageSegmentResult(
        assignment=assignment, artifacts=(mask_artifact, components),
        metrics={"total_ms": round((clock() - started) * 1000.0, 1), "regions": len(assignment.observations),
                 "dropped_regions": assignment.dropped_region_count})


# ---------------------------------------------------------------------------
# The Kafka handler
# ---------------------------------------------------------------------------

def _read_optional(storage: Any, object_uri: str, what: str) -> bytes | None:
    """Bytes by URI, or None when the object does not exist. Any other storage failure is worth a retry."""
    try:
        return storage.read(storage_key(object_uri))
    except Exception as exc:  # noqa: BLE001 - classified below
        if is_missing(exc):
            return None
        raise TransientError("artifact_read_failed", f"could not read {what}: {type(exc).__name__}") from exc


def make_damage_handler(segmenter: DamageSegmenter, storage: Any, versions: Mapping[str, str],
                        assignment_config: AssignmentConfig | None = None):
    """Factory creating a ConsumerRuntime handler for ``cmev.cmd.damage-segment.v1``.

    ``versions`` are the stage versions of the checkpoint ``segmenter`` loaded. A command
    pinned to anything else, such as the fixture tags, is refused, so no row is recorded
    under a version that did not produce it.
    """
    served = dict(versions)
    rules = assignment_config or load_assignment_config()

    def handler(ctx: Context) -> dict[str, Any]:
        env, payload = ctx.envelope, ctx.payload
        if dict(env.versions) != served:
            raise PermanentError("model_version_unsupported",
                                 "this worker serves only commands pinned to the versions it loaded")
        if "accepted_parts" not in payload:
            raise PermanentError("accepted_parts_missing",
                                 "the command does not say which parts M1 accepted on this photograph")
        photo_ref, mask_ref = payload["photo"], payload["part_mask_ref"]
        photo_id = photo_ref["file_id"]
        photo = read_object(storage, photo_ref["object_uri"], f"photo {photo_id}")
        # A missing part mask never erases a damage detection: the damage stays, with unknown part.
        part_mask = _read_optional(storage, mask_ref["object_uri"], f"the part mask of {photo_id}")
        request = DamageSegmentRequest(
            claim_id=env.claim_id, input_revision=env.input_revision, job_key=env.job_key, photo_id=photo_id,
            photo=photo, photo_sha256=photo_ref["sha256"], part_mask=part_mask,
            part_mask_ref=MaskRef(**mask_ref, source_photo_id=photo_id),
            transform=ImageTransform.model_validate(payload["transform"]),
            accepted_parts=tuple(payload["accepted_parts"]), versions=env.versions, provenance=ctx.provenance(),
            object_uri_prefix=storage.uri(""))
        try:
            result = run_damage_segmentation(request, segmenter, rules)
        except DamageInputError as exc:
            raise PermanentError(exc.reason_code, exc.message) from exc

        for artifact in result.artifacts:
            storage.write(artifact.key, artifact.data, artifact.media_type)
        insert_records(ctx.session, [("damage_observation", o) for o in result.observations], claim_id=env.claim_id,
                       input_revision=env.input_revision, stage="damage", job_key=env.job_key, now=ctx.now)
        event_payload = result.event_payload()
        job = ctx.job or {}
        ctx.emit(DAMAGE_EVENT_TOPIC, build_message(
            DAMAGE_EVENT_TOPIC, claim_id=env.claim_id, input_revision=env.input_revision, task=job["task"],
            versions=env.versions, target=job["target"], attempt_epoch=job["attempt_epoch"], trace_id=env.trace_id,
            occurred_at=ctx.now, payload=event_payload, causation_id=env.dedup_key, provenance=ctx.provenance()))
        return {"photo_id": photo_id, "observation_ids": event_payload["observation_ids"],
                "damage_mask_ref": event_payload["damage_mask_ref"],
                "unknown_part_count": event_payload["unknown_part_count"],
                "dropped_region_count": event_payload["dropped_region_count"],
                "filtering": event_payload["filtering"],
                "part_mask_present": part_mask is not None}

    return handler


__all__ = ["DAMAGE_EVENT_TOPIC", "DamageArtifact", "DamageInputError", "DamageSegmentRequest", "DamageSegmentResult",
           "DamageSegmenter", "ModelUnavailable", "load_damage_segmenter", "make_damage_handler",
           "run_damage_segmentation", "verify_damage_checkpoint"]
