"""M5 line-item extraction: the committed deterministic parser over M4 text boxes.

See docs/specs/module-05-line-item-extraction.md. ``parse_pages`` is pure: contract
``DocumentPage`` records in, validated ``LineItem`` and ``DeclarationCompleteness``
records out. Configuration lives in ``configs/pipeline/m5_layout_families.yaml`` and
``configs/pipeline/m5_estimate_vocabulary.yaml``; both are PROPOSED until the team's
day-1 freeze. The Kafka consumer shell and LayoutLMv3 (stretch S1) are not here.
"""
from .completeness import CompletenessFacts, confirm_declaration_completeness, decide_completeness
from .config import LayoutFamilyConfig, load_layout_families
from .parser import (
    ENGINE,
    M5_CODE_VERSION,
    TASK,
    FieldResult,
    MissingPage,
    PageParse,
    ParsedRow,
    ParseResult,
    UnparsedRegion,
    entry_id_for,
    line_items_event_payload,
    merged_versions,
    parse_pages,
    pen_mark_row_boxes,
)
from .values import ParsedValue, parse_decimal
from .vocabulary import EstimateVocabulary, MappingResult, SideResult, load_estimate_vocabulary

__all__ = [
    "ENGINE", "M5_CODE_VERSION", "TASK", "CompletenessFacts", "EstimateVocabulary", "FieldResult",
    "LayoutFamilyConfig", "MappingResult", "MissingPage", "PageParse", "ParseResult", "ParsedRow", "ParsedValue",
    "SideResult", "UnparsedRegion", "confirm_declaration_completeness", "decide_completeness", "entry_id_for",
    "line_items_event_payload", "load_estimate_vocabulary", "load_layout_families", "merged_versions",
    "parse_decimal", "parse_pages", "pen_mark_row_boxes",
]
