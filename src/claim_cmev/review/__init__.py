"""M9 review rules as a pure library: actions, finalize gate, print payload and overlays.

See docs/specs/module-09-review-report.md. ``cmev-api`` loads a ``ReviewState``, calls
these functions and persists what they return; the functions do no I/O besides reading
the versioned configuration once, and never read a clock.
"""
from .actions import (
    ActionError,
    ActionOutcome,
    BatchOutcome,
    ConflictInfo,
    apply_review_action,
    apply_review_batch,
    carryable_dismissals,
    classify_review_action,
    fold_action,
    replay_review_actions,
    replay_review_events,
    request_hash,
    rerun_stages,
)
from .config import ReviewConfig, default_review_config, load_review_config
from .finalize import (
    Blocker,
    ConditionResult,
    FinalizeOutcome,
    FinalizePreconditions,
    evaluate_finalize_preconditions,
    finalize_review,
)
from .overlays import box_to_pixels, render_mark_crop, render_page_highlight, render_photo_overlay
from .report import PrintPayload, PrintRefused, build_print_payload, default_reason_catalogue
from .state import (
    BRANCH_STAGES,
    STAGE_ORDER,
    MarkView,
    ReassessmentPlan,
    RecordedAction,
    ReviewActionRequest,
    ReviewState,
    open_review,
)

__all__ = [
    "BRANCH_STAGES", "STAGE_ORDER", "ActionError", "ActionOutcome", "BatchOutcome", "Blocker", "ConditionResult",
    "ConflictInfo", "FinalizeOutcome", "FinalizePreconditions", "MarkView", "PrintPayload", "PrintRefused",
    "ReassessmentPlan", "RecordedAction", "ReviewActionRequest", "ReviewConfig", "ReviewState", "apply_review_action",
    "apply_review_batch", "box_to_pixels", "build_print_payload", "carryable_dismissals", "classify_review_action",
    "default_reason_catalogue", "default_review_config", "evaluate_finalize_preconditions", "finalize_review",
    "fold_action", "load_review_config", "open_review", "render_mark_crop", "render_page_highlight",
    "render_photo_overlay", "replay_review_actions", "replay_review_events", "request_hash", "rerun_stages",
]
