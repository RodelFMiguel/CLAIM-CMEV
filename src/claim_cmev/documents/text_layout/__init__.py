"""M4 page reading: rasterise, correct geometry, OCR, quality and transforms.

See docs/specs/module-04-page-reading.md. The PaddleOCR engine lives in ``.paddle``
and is imported only when constructed; nothing here needs Paddle to import.
"""
from .adapter import (
    M4_CODE_VERSION,
    PageArtifact,
    PageFileInput,
    PageReadRequest,
    PageReadResult,
    page_read_event_payload,
    reading_order,
    run_page_reading,
)
from .config import PageReadingConfig, load_page_reading_config
from .geometry import GeometryResult, correct_page_geometry, detect_page_boundary, estimate_skew
from .ocr import (
    GranularityCheck,
    OcrEngine,
    OcrEngineError,
    OcrEngineInfo,
    OcrRegion,
    StubOcrEngine,
    check_granularity,
)
from .page_transform import PageTransform
from .raster import page_id_for, render_pages

__all__ = [
    "M4_CODE_VERSION", "GeometryResult", "GranularityCheck", "OcrEngine", "OcrEngineError", "OcrEngineInfo",
    "OcrRegion", "PageArtifact", "PageFileInput", "PageReadRequest", "PageReadResult", "PageReadingConfig",
    "PageTransform", "StubOcrEngine", "check_granularity", "correct_page_geometry", "detect_page_boundary",
    "estimate_skew", "load_page_reading_config", "page_id_for", "page_read_event_payload", "reading_order",
    "render_pages", "run_page_reading",
]
