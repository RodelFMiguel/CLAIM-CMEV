"""M6 pen-mark detector: load a trained Faster R-CNN from a registry entry and propose marks.

Module-06 step 2. The detector proposes boxes of two classes, ``exclusion`` and
``price_change``, on M4's corrected page. It decides nothing: ``link_detections`` applies
the per-class score thresholds of the linking configuration, links each box to a row only
when the geometry is unambiguous, and creates every mark ``pending``. A ``price_change``
box says where a handwritten amount is; it is never a recognised number.

``load_penmark_detector`` verifies the entry before it builds a network, in the manner of
``claim_cmev.vision.registry``: another model, architecture, class map or preprocessing is
refused, never adapted. The network is built from the settings recorded in the entry's
``preprocessing.json``, because several of them are not in the weight file. The frozen
batch-norm ``eps`` is the dangerous one: a strict load succeeds with the library default
and the boxes move.

What the verification is and is not. The manifest carries the SHA-256 of the weights and of
``preprocessing.json``, so an *accidental* change to either is detected. It is not a
signature: someone who edits ``preprocessing.json`` and also writes its new hash into the
manifest, or who sets ``status: approved`` by hand, is not stopped by anything in this
file. Approval is a recorded human decision made outside the entry; who may write to the
registry is a deployment control.

``torch``, ``torchvision`` and ``safetensors`` are imported inside the functions that need
them, so ``cmev-api`` and the lean worker can import this package without them;
``load_penmark_detector`` refuses with ``model_runtime_missing`` where they are absent.
The Kafka consumer, the artifact store and the overlay renderer are not implemented here.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any, Literal, Protocol, get_args, runtime_checkable

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from claim_cmev.contracts.documents import MarkType
from claim_cmev.vision.registry import ModelUnavailable, sha256_file

from .linking import Detection

MODEL_ID = "penmarks"
WEIGHTS_FILE = "model.safetensors"
ARCHITECTURE = "fasterrcnn_resnet50_fpn"
SUPPORTED_ARCHITECTURES = (ARCHITECTURE,)
LABEL_SCHEMA_VERSION = "penmarks-labels/1.0.0"
PREPROCESSING_VERSION = "penmarks-preprocessing/1.0.0"
MARK_TAXONOMY_VERSION = "penmark-types-1.0.0"
"""Names the two mark types of ``contracts.documents.MarkType``; there is no taxonomy file for them."""
LABELS: dict[int, str] = {0: "background", 1: "exclusion", 2: "price_change"}
"""The detector's class numbering. Index 0 is torchvision's background class."""
STATUSES = ("candidate", "approved")
BOX_NORM_DECIMALS = 6  # M4 stores TextBox.box_norm to six decimals
_FPN_LEVELS = 5  # ResNet-50 FPN: four backbone stages plus the pooled level

assert tuple(LABELS[i] for i in (1, 2)) == get_args(MarkType), "the detector classes are the contract's mark types"


class DetectorSettings(BaseModel):
    """What the served detector applies to a page: the content of ``preprocessing.json``.

    ``detect`` feeds an RGB uint8 page scaled to 0-1; the network normalises, resizes and
    pads it. Everything from ``image_mean`` down configures that network and is absent from
    the weight file, so it is recorded here and the network is built from it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    preprocessing_version: Literal["penmarks-preprocessing/1.0.0"] = PREPROCESSING_VERSION
    architecture: Literal["fasterrcnn_resnet50_fpn"] = ARCHITECTURE
    input_frame: Literal["m4_corrected_page"] = "m4_corrected_page"
    color_space: Literal["RGB"] = "RGB"
    input_dtype: Literal["uint8"] = "uint8"
    value_range: tuple[float, float] = (0.0, 1.0)
    """Range of the float tensor given to the network: the uint8 page divided by 255."""
    normalisation: Literal["inside_model"] = "inside_model"
    image_mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    image_std: tuple[float, float, float] = (0.229, 0.224, 0.225)
    resize_policy: Literal["shorter_side_to_min_size_unless_longer_exceeds_max_size"] = \
        "shorter_side_to_min_size_unless_longer_exceeds_max_size"
    min_size: int = Field(default=800, ge=32)
    max_size: int = Field(default=1333, ge=32)
    anchor_sizes: tuple[tuple[int, ...], ...] = ((32,), (64,), (128,), (256,), (512,))
    """Anchor side lengths in resized-image pixels, one tuple per FPN level, finest level first."""
    anchor_aspect_ratios: tuple[float, ...] = (0.5, 1.0, 2.0)
    """Height divided by width, the torchvision convention: 0.5 is twice as wide as tall."""
    frozen_batchnorm_eps: float = Field(default=0.0, ge=0)
    rpn_pre_nms_top_n: int = Field(default=1000, ge=1)
    rpn_post_nms_top_n: int = Field(default=1000, ge=1)
    rpn_nms_iou: float = Field(default=0.7, gt=0, le=1)
    rpn_score_floor: float = Field(default=0.0, ge=0, le=1)
    score_floor: float = Field(default=0.05, ge=0, lt=1)
    """Boxes below this score are never returned. It is not a decision threshold."""
    nms_iou: float = Field(default=0.5, gt=0, le=1)
    max_detections_per_page: int = Field(default=50, ge=1)
    output_frame: Literal["corrected_page_pixels_xyxy"] = "corrected_page_pixels_xyxy"
    box_norm_decimals: Literal[6] = BOX_NORM_DECIMALS

    @model_validator(mode="after")
    def _coherent(self) -> DetectorSettings:
        if self.value_range != (0.0, 1.0):
            raise ValueError(f"the detector feeds pixel values in 0-1, not {self.value_range}")
        if self.max_size < self.min_size:
            raise ValueError("max_size must not be below min_size")
        if any(value <= 0 for value in self.image_std):
            raise ValueError("image_std must be positive")
        if len(self.anchor_sizes) != _FPN_LEVELS:
            raise ValueError(f"anchor_sizes needs one tuple for each of the {_FPN_LEVELS} FPN levels")
        if not self.anchor_sizes[0] or any(len(level) != len(self.anchor_sizes[0]) for level in self.anchor_sizes):
            raise ValueError("every FPN level needs the same, non-zero number of anchor sizes: one RPN head serves all")
        if any(size <= 0 for level in self.anchor_sizes for size in level):
            raise ValueError("anchor sizes must be positive")
        if not self.anchor_aspect_ratios or any(ratio <= 0 for ratio in self.anchor_aspect_ratios):
            raise ValueError("anchor_aspect_ratios must be a non-empty list of positive numbers")
        return self

    @property
    def anchors_per_location(self) -> int:
        return len(self.anchor_sizes[0]) * len(self.anchor_aspect_ratios)

    def to_record(self) -> dict[str, Any]:
        """The JSON content of ``preprocessing.json``."""
        return self.model_dump(mode="json")

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DetectorSettings:
        """Parse a record strictly: an unknown or a missing key is an error, never a default."""
        missing, unknown = sorted(set(cls.model_fields) - set(record)), sorted(set(record) - set(cls.model_fields))
        if missing or unknown:
            raise ValueError(f"missing keys {missing}, unknown keys {unknown}")
        return cls.model_validate(dict(record))


def label_schema_record() -> dict[str, Any]:
    """The JSON content of ``label_schema.json`` for this detector."""
    return {
        "schema_version": LABEL_SCHEMA_VERSION, "taxonomy_version": MARK_TAXONOMY_VERSION,
        "background_class_id": 0, "background_class_code": "background", "num_classes": len(LABELS),
        "id_to_code": {str(index): code for index, code in LABELS.items()},
        "side_resolution": "not_applicable",
        "not_targets": ["acceptance_tick", "review_note", "any other pen mark"],
        "note": "A price_change box locates a handwritten amount. It is never a recognised number.",
    }


@runtime_checkable
class PenMarkDetector(Protocol):
    """Anything that proposes pen-mark boxes on one corrected page."""

    def detect(self, image: NDArray[np.uint8], *, page_id: str) -> list[Detection]:
        """Proposals for an RGB uint8 page ``[height, width, 3]``, boxes in the page's 0-1 frame."""
        ...


# --------------------------------------------------------------------------- the network

def build_model(settings: DetectorSettings) -> Any:
    """The Faster R-CNN ResNet-50 FPN that ``settings`` describes, with untrained weights.

    Training and serving both build the network here, so they cannot drift apart. Nothing is
    downloaded. ``FrozenBatchNorm2d.eps`` is set from the settings: torchvision's COCO
    weights were trained with 0.0, the constructor default is 1e-5 and the value is not
    stored with the weights.
    """
    from torchvision.models.detection import FasterRCNN
    from torchvision.models.detection.anchor_utils import AnchorGenerator
    from torchvision.models.detection.backbone_utils import resnet_fpn_backbone
    from torchvision.models.detection.rpn import RPNHead
    from torchvision.ops.misc import FrozenBatchNorm2d

    backbone = resnet_fpn_backbone(backbone_name="resnet50", weights=None, norm_layer=FrozenBatchNorm2d,
                                   trainable_layers=3)
    anchors = AnchorGenerator(sizes=settings.anchor_sizes,
                              aspect_ratios=(settings.anchor_aspect_ratios,) * len(settings.anchor_sizes))
    model = FasterRCNN(
        backbone, num_classes=len(LABELS), min_size=settings.min_size, max_size=settings.max_size,
        image_mean=list(settings.image_mean), image_std=list(settings.image_std),
        rpn_anchor_generator=anchors, rpn_head=RPNHead(backbone.out_channels, settings.anchors_per_location),
        rpn_pre_nms_top_n_test=settings.rpn_pre_nms_top_n, rpn_post_nms_top_n_test=settings.rpn_post_nms_top_n,
        rpn_nms_thresh=settings.rpn_nms_iou, rpn_score_thresh=settings.rpn_score_floor,
        box_score_thresh=settings.score_floor, box_nms_thresh=settings.nms_iou,
        box_detections_per_img=settings.max_detections_per_page)
    for module in model.modules():
        if isinstance(module, FrozenBatchNorm2d):
            module.eps = settings.frozen_batchnorm_eps
    return model


def applied_settings(model: Any, settings: DetectorSettings) -> DetectorSettings:
    """``settings`` with every network value replaced by what ``model`` actually applies."""
    from torchvision.ops.misc import FrozenBatchNorm2d

    eps = sorted({float(m.eps) for m in model.modules() if isinstance(m, FrozenBatchNorm2d)})
    generator = model.rpn.anchor_generator
    return settings.model_copy(update={
        "min_size": int(model.transform.min_size[0]), "max_size": int(model.transform.max_size),
        "image_mean": tuple(float(v) for v in model.transform.image_mean),
        "image_std": tuple(float(v) for v in model.transform.image_std),
        "anchor_sizes": tuple(tuple(int(s) for s in level) for level in generator.sizes),
        "anchor_aspect_ratios": tuple(float(r) for r in generator.aspect_ratios[0]),
        "frozen_batchnorm_eps": eps[0] if len(eps) == 1 else -1.0,
        "rpn_pre_nms_top_n": int(model.rpn._pre_nms_top_n["testing"]),
        "rpn_post_nms_top_n": int(model.rpn._post_nms_top_n["testing"]),
        "rpn_nms_iou": float(model.rpn.nms_thresh), "rpn_score_floor": float(model.rpn.score_thresh),
        "score_floor": float(model.roi_heads.score_thresh), "nms_iou": float(model.roi_heads.nms_thresh),
        "max_detections_per_page": int(model.roi_heads.detections_per_img)})


def detections_from_arrays(page_id: str, boxes: Any, labels: Any, scores: Any, *, width: int,
                           height: int) -> list[Detection]:
    """``Detection`` records from pixel boxes ``[N, 4]`` (x0, y0, x1, y1) on a ``width`` x ``height`` page.

    Boxes are divided by the page size, clipped to 0-1 and rounded like M4's ``box_norm``.
    The order is fixed: highest score first, then top to bottom, left to right.
    """
    boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
    labels, scores = np.asarray(labels, dtype=int).reshape(-1), np.asarray(scores, dtype=float).reshape(-1)
    if not len(boxes) == len(labels) == len(scores):
        raise ValueError("boxes, labels and scores must have one entry per detection")
    unknown = sorted(set(labels.tolist()) - (set(LABELS) - {0}))
    if unknown:
        raise ValueError(f"label {unknown} is not a mark class; the detector returns {dict(list(LABELS.items())[1:])}")
    norm = np.round(np.clip(boxes / np.array([width, height, width, height], dtype=float), 0.0, 1.0),
                    BOX_NORM_DECIMALS)
    order = stable_order(boxes, labels, scores)
    return [Detection(page_id=page_id, box_norm=tuple(float(v) for v in norm[i]), mark_type=LABELS[int(labels[i])],
                      score=min(1.0, max(0.0, float(scores[i])))) for i in order]


def stable_order(boxes: NDArray[np.floating], labels: NDArray[np.integer],
                  scores: NDArray[np.floating]) -> NDArray[np.intp]:
    """Indexes sorted by descending score, then y0, x0, y1, x1 and class."""
    if not len(scores):
        return np.zeros(0, dtype=np.intp)
    return np.lexsort((labels, boxes[:, 2], boxes[:, 3], boxes[:, 0], boxes[:, 1], -scores))


class TorchFasterRCNNDetector:
    """A verified pen-mark detector in evaluation mode. Build it with ``load_penmark_detector``."""

    def __init__(self, model: Any, settings: DetectorSettings, manifest: Mapping[str, Any], device: str = "cpu"):
        import torch

        self._torch = torch
        self.device = torch.device(device)
        self.model = model.to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        self.settings = settings
        self.manifest = dict(manifest)
        self.model_version: str = manifest["version"]
        self.status: str = manifest["status"]

    @property
    def versions(self) -> dict[str, str]:
        """Version keys to stamp on every mark this detector proposes."""
        return {"penmark_model": self.model_version, "penmark_preprocessing": self.settings.preprocessing_version}

    def predict(self, image: NDArray[np.uint8]) -> tuple[NDArray[np.float64], NDArray[np.int64], NDArray[np.float64]]:
        """Boxes ``[N, 4]`` in the pixels of ``image``, class indexes and scores, in a fixed order.

        ``image`` is the corrected page as RGB uint8 ``[height, width, 3]``. M4 works on
        OpenCV arrays, which are BGR: a caller holding one converts it first
        (``image[..., ::-1]``). The stored ``rectified.png`` decodes to RGB with Pillow. The
        channel order cannot be checked here, so a BGR page is silently a different picture.

        torchvision resizes and pads the page internally and maps its boxes back before
        returning them. They are already in the frame of ``image`` and are not rescaled here.
        The network applies class-wise NMS, the score floor and the per-page cap itself.
        """
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 \
                or 0 in image.shape:
            raise ValueError("the page must be an RGB uint8 array of shape [height, width, 3]")
        torch = self._torch
        pixels = torch.from_numpy(np.ascontiguousarray(image)).to(self.device).permute(2, 0, 1).float().div(255.0)
        with torch.inference_mode():
            output = self.model([pixels])[0]
        boxes = output["boxes"].detach().cpu().numpy().astype(np.float64).reshape(-1, 4)
        labels = output["labels"].detach().cpu().numpy().astype(np.int64)
        scores = output["scores"].detach().cpu().numpy().astype(np.float64)
        order = stable_order(boxes, labels, scores)
        return boxes[order], labels[order], scores[order]

    def detect(self, image: NDArray[np.uint8], *, page_id: str) -> list[Detection]:
        """Proposals for one corrected page; see ``predict`` for the image convention.

        Only the low score floor of the entry is applied. The per-class decision thresholds
        belong to the linking configuration and are applied by ``link_detections``.
        """
        boxes, labels, scores = self.predict(image)
        return detections_from_arrays(page_id, boxes, labels, scores, width=image.shape[1], height=image.shape[0])


# --------------------------------------------------------------------------- the registry entry

def _read_json(path: Path, reason_code: str) -> dict[str, Any]:
    if not path.is_file():
        raise ModelUnavailable(reason_code, f"{path} does not exist")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_penmark_entry(model_dir: str | Path, *, expected: Mapping[str, Any] | None = None,
                         allow_candidate: bool = True) -> tuple[dict[str, Any], DetectorSettings]:
    """Check a registry entry without loading a model library; returns its manifest and settings.

    ``expected`` holds preprocessing settings a deployment pins, for example ``min_size``;
    an entry that records another value is refused. Raises ``ModelUnavailable``.
    """
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise ModelUnavailable("model_not_found", f"no registry entry at {model_dir}")
    manifest = _read_json(model_dir / "manifest.json", "model_manifest_missing")
    if manifest.get("model_id") != MODEL_ID:
        raise ModelUnavailable("model_id_mismatch", f"the manifest names model {manifest.get('model_id')!r}, "
                               f"this loader serves {MODEL_ID!r}")
    if manifest.get("architecture") not in SUPPORTED_ARCHITECTURES:
        raise ModelUnavailable("architecture_unsupported", f"the manifest names {manifest.get('architecture')!r}; "
                               f"this loader builds {', '.join(SUPPORTED_ARCHITECTURES)}")
    if manifest.get("status") not in STATUSES:
        raise ModelUnavailable("model_status_invalid", f"status {manifest.get('status')!r} is not one of {STATUSES}")
    if manifest["status"] != "approved" and not allow_candidate:
        raise ModelUnavailable("model_status_not_approved", f"{manifest.get('version')!r} is a candidate")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ModelUnavailable("model_version_missing", "the manifest names no version")
    weights = model_dir / WEIGHTS_FILE
    if not weights.is_file():
        raise ModelUnavailable("model_weights_missing", f"no {WEIGHTS_FILE} in {model_dir}")
    if sha256_file(weights) != manifest.get("weights_sha256"):
        raise ModelUnavailable("model_weights_hash_mismatch", f"{WEIGHTS_FILE} does not match its manifest hash")

    schema = _read_json(model_dir / "label_schema.json", "label_schema_missing")
    if manifest.get("label_schema_version") != LABEL_SCHEMA_VERSION:
        raise ModelUnavailable("label_schema_version_mismatch", f"the manifest names "
                               f"{manifest.get('label_schema_version')!r}, this loader reads {LABEL_SCHEMA_VERSION!r}")
    if schema.get("id_to_code") != {str(index): code for index, code in LABELS.items()}:
        raise ModelUnavailable("label_map_mismatch", f"the entry's classes are {schema.get('id_to_code')!r}, "
                               f"not exactly {LABELS!r}; classes are never renumbered")

    preprocessing_path = model_dir / "preprocessing.json"
    record = _read_json(preprocessing_path, "preprocessing_missing")
    if sha256_file(preprocessing_path) != manifest.get("preprocessing_sha256"):
        raise ModelUnavailable("preprocessing_hash_mismatch", "preprocessing.json is not the record the manifest "
                               "was written with, so it may not describe how the weights were trained")
    try:
        settings = DetectorSettings.from_record(record)
    except (ValidationError, ValueError) as exc:
        raise ModelUnavailable("preprocessing_mismatch", "preprocessing.json does not describe what this detector "
                               f"applies: {exc}") from exc
    if manifest.get("preprocessing_version") != settings.preprocessing_version \
            or manifest["architecture"] != settings.architecture:
        raise ModelUnavailable("preprocessing_mismatch", "the manifest and preprocessing.json name different "
                               "preprocessing versions or architectures")
    pinned = dict(expected or {})
    not_settings = sorted(set(pinned) - set(DetectorSettings.model_fields))
    if not_settings:
        raise ValueError(f"{not_settings} is not a preprocessing setting; known: {sorted(DetectorSettings.model_fields)}")
    differing = [key for key, value in pinned.items() if json.loads(json.dumps(value)) != record[key]]
    if differing:
        raise ModelUnavailable("preprocessing_mismatch", "; ".join(
            f"{key}: trained with {record[key]!r}, served with {pinned[key]!r}" for key in differing))
    return manifest, settings


def _model_runtime() -> Any:
    """Import the model libraries and return ``safetensors.torch.load_file``; refuse where they are absent."""
    try:
        import torch  # noqa: F401
        import torchvision  # noqa: F401
        from safetensors.torch import load_file
    except ImportError as exc:
        raise ModelUnavailable("model_runtime_missing", "loading a pen-mark detector needs torch, torchvision and "
                               f"safetensors, and this environment cannot import them ({exc}). "
                               "verify_penmark_entry still checks an entry without them") from exc
    return load_file


def load_penmark_detector(model_dir: str | Path, *, device: str = "cpu", expected: Mapping[str, Any] | None = None,
                          allow_candidate: bool = True) -> TorchFasterRCNNDetector:
    """Verify registry entry ``model_dir`` and return its detector on ``device``.

    The entry is checked first (``verify_penmark_entry``). The network is then built from
    the recorded settings and the weights are loaded strictly: a tensor that is missing,
    extra or of another shape refuses the entry. Raises ``ModelUnavailable``, which a worker
    treats as a start-up error and never as a claim result; that includes an environment
    without the model libraries (``model_runtime_missing``).

    The hash checks catch accidental edits of the weights and of ``preprocessing.json``.
    They do not authenticate the entry: a person who rewrites a record together with its
    hash in the manifest, or the ``status`` field, passes. ``allow_candidate=False`` trusts
    whoever wrote ``status``; approval itself is a human decision recorded elsewhere.
    """
    manifest, settings = verify_penmark_entry(model_dir, expected=expected, allow_candidate=allow_candidate)
    load_file = _model_runtime()

    model = build_model(settings)
    state = load_file(str(Path(model_dir) / WEIGHTS_FILE))
    wanted = {name: tuple(tensor.shape) for name, tensor in model.state_dict().items()}
    found = {name: tuple(tensor.shape) for name, tensor in state.items()}
    problems = ([f"missing {name}" for name in sorted(set(wanted) - set(found))]
                + [f"unexpected {name}" for name in sorted(set(found) - set(wanted))]
                + [f"{name} is {found[name]}, the recorded settings need {wanted[name]}"
                   for name in sorted(set(wanted) & set(found)) if wanted[name] != found[name]])
    if problems:
        raise ModelUnavailable("model_weights_incomplete", f"{WEIGHTS_FILE} does not fit the network the entry "
                               f"records ({len(problems)} tensors): " + "; ".join(problems[:6]))
    model.load_state_dict(state, strict=True)
    if applied_settings(model, settings) != settings:
        raise ModelUnavailable("preprocessing_mismatch", "the network that was built does not apply the recorded "
                               "settings; the installed torchvision may construct it differently")
    return TorchFasterRCNNDetector(model, settings, manifest, device=device)


__all__ = ["ARCHITECTURE", "LABELS", "LABEL_SCHEMA_VERSION", "MARK_TAXONOMY_VERSION", "MODEL_ID",
           "PREPROCESSING_VERSION", "SUPPORTED_ARCHITECTURES", "WEIGHTS_FILE", "DetectorSettings", "PenMarkDetector",
           "TorchFasterRCNNDetector", "applied_settings", "build_model", "detections_from_arrays",
           "label_schema_record", "load_penmark_detector", "stable_order", "verify_penmark_entry"]
