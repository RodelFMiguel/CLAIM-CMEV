"""M6 pen-mark recognition: geometric linking and the mark state machine (pure).

See docs/specs/module-06-pen-mark-recognition.md. ``link_marks`` turns detector boxes
into ``pending`` marks linked to M5 rows only when unambiguous; ``apply_mark_action``
applies surveyor decisions for ``cmev-api``; the row helpers give M8 and M9 each row's
derived mark state. ``load_penmark_detector`` loads a trained Faster R-CNN from a verified
registry entry; it imports torch only when called, so this package imports without it.
The Kafka consumer and overlay rendering are not implemented here, and no detection or
linking accuracy on real marked pages has been measured.
"""
from .config import LinkingConfig, load_linking_config
from .detector import DetectorSettings, PenMarkDetector, TorchFasterRCNNDetector, load_penmark_detector
from .linking import (
    Detection,
    LinkingResult,
    MarkCandidate,
    MergedDetection,
    RowScore,
    decide_link,
    link_detections,
    link_marks,
    score_row,
)
from .rows import (
    WITHHOLDING_STATES,
    RowBox,
    RowMarkState,
    conflicting_entry_ids,
    marks_for_entry,
    row_box_of,
    row_mark_state,
    row_mark_states,
    unresolved_marks,
)
from .state import (
    ACTION_ERROR_CODES,
    MARK_ACTION_TYPES,
    MarkAction,
    MarkTransition,
    apply_mark_action,
    apply_review_event,
    mark_action_from_review_event,
    transition_mark,
)

__all__ = [
    "ACTION_ERROR_CODES", "MARK_ACTION_TYPES", "WITHHOLDING_STATES", "Detection", "DetectorSettings", "LinkingConfig",
    "LinkingResult", "MarkAction", "MarkCandidate", "MarkTransition", "MergedDetection", "PenMarkDetector", "RowBox",
    "RowMarkState", "RowScore", "TorchFasterRCNNDetector", "apply_mark_action", "apply_review_event",
    "conflicting_entry_ids", "decide_link", "link_detections", "link_marks", "load_linking_config",
    "load_penmark_detector", "mark_action_from_review_event", "marks_for_entry", "row_box_of", "row_mark_state",
    "row_mark_states", "score_row", "transition_mark", "unresolved_marks",
]
