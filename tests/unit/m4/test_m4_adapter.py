"""run_page_reading: granularity assertion, failures, identity, versions, artifacts and events."""
import hashlib
import json

import pytest

from claim_cmev.contracts.events.registry import load_example, validate_message
from claim_cmev.documents.text_layout import (
    OcrRegion,
    StubOcrEngine,
    page_id_for,
    page_read_event_payload,
    reading_order,
    run_page_reading,
)
from claim_cmev.documents.text_layout.synthetic import render_estimate_page
from m4_support import FILE_ID, line_quads, m4_config, pdf_bytes, png_bytes, regions_in_rectified, request_for

import numpy as np

CFG = m4_config()
PAGE = render_estimate_page(1240)
PNG = png_bytes(PAGE.image)
LINES = line_quads(PAGE, roles=("column_header", "cell", "total"))


def _regions(confidence=0.95, level="line"):
    return regions_in_rectified(LINES, np.eye(3), confidence, level)


def _run(regions=None, config=CFG, data=PNG, media_type="image/png", **request_kwargs):
    engine = StubOcrEngine(_regions() if regions is None else regions)
    return run_page_reading(request_for(data, media_type, **request_kwargs), engine, config), engine


def test_matching_granularity_reads_every_region_once():
    result, _ = _run()
    assert result.processing_status == "succeeded" and not result.reasons
    [page] = result.pages
    assert page.quality.state == "complete" and page.text_granularity == "line"
    assert len(page.text_boxes) == len(LINES)  # one box per engine region: nothing split or merged
    assert {b.granularity for b in page.text_boxes} == {"line"}
    assert sorted(b.text for b in page.text_boxes) == sorted(t for t, _ in LINES)
    assert [b.order_index for b in page.text_boxes] == list(range(len(LINES)))


def test_line_engine_can_never_produce_word_level_output():
    """The failing input: an engine declared 'line' that returns word-level regions."""
    result, _ = _run(_regions(level="word"))
    [page] = result.pages
    assert page.quality.state == "unreadable" and page.text_boxes == []
    assert "granularity_mismatch" in page.quality.reasons
    assert "engine_granularity_contradiction" in page.quality.reasons
    assert page.text_granularity == "word"  # what the engine actually gave, recorded
    assert result.processing_status == "failed"


def test_word_boxes_nested_inside_line_boxes_fail_the_page():
    (text, quad), *_ = LINES
    x0, y0 = quad[0]
    x1, y1 = quad[2]
    word = OcrRegion(((x0, y0), ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1), (x0, y1)), text.split()[0], 0.9, "line")
    result, _ = _run(_regions() + [word])
    [page] = result.pages
    assert page.quality.state == "unreadable"
    assert {"granularity_mismatch", "nested_text_boxes"} <= set(page.quality.reasons)
    assert page.text_granularity == "mixed" and page.text_boxes == []


def test_granularity_other_than_configured_fails_the_page():
    config = m4_config(page={"expected_text_box_granularity": "word"})
    result, _ = _run(config=config)
    [page] = result.pages
    assert page.quality.state == "unreadable"
    assert {"granularity_mismatch", "unexpected_granularity"} <= set(page.quality.reasons)


def test_no_text_is_unreadable_never_an_empty_success():
    result, _ = _run([])
    [page] = result.pages
    assert page.quality.state == "unreadable" and "ocr_no_text" in page.quality.reasons
    assert page.text_boxes == [] and page.mean_text_confidence is None
    assert page.mean_text_confidence_reason == "no_text_boxes"
    assert result.processing_status == "failed" and result.reasons[0].code == "page_unreadable"


def test_engine_crash_marks_the_page_unreadable_and_retryable():
    engine = StubOcrEngine(error=MemoryError("simulated"))
    result = run_page_reading(request_for(PNG), engine, CFG)
    [page] = result.pages
    assert page.quality.state == "unreadable" and "ocr_failed" in page.quality.reasons
    assert result.retryable and result.processing_status == "failed"


@pytest.mark.parametrize("bad", [
    OcrRegion(((0, 0), (10, 0), (10, 10)), "x", 0.9, "line"),
    OcrRegion(((0, 0), (10, 0), (10, 10), (0, 10)), "x", 1.7, "line"),
])
def test_invalid_engine_output_is_rejected(bad):
    result, _ = _run(_regions() + [bad])
    [page] = result.pages
    assert page.quality.state == "unreadable" and "ocr_output_invalid" in page.quality.reasons


def test_hash_mismatch_fails_the_file_without_reading_it():
    result, engine = _run(sha256="0" * 64)
    assert result.processing_status == "failed" and result.reasons[0].code == "artifact_hash_mismatch"
    assert result.retryable and result.pages == () and result.artifacts == () and engine.calls == 0
    [reading] = result.page_readings
    assert reading["page_status"] == "unreadable" and reading["page_id"] == page_id_for(FILE_ID, 1)


def test_pinned_version_mismatch_fails_before_any_work():
    result, engine = _run(versions={"code": "test", "ocr_version": "9.9.9"})
    assert result.processing_status == "failed" and result.reasons[0].code == "version_mismatch"
    assert engine.calls == 0 and result.page_readings == ()


def test_fixture_engine_output_cannot_be_labelled_real():
    real = {"source_kind": "real", "runtime_profile": "lean", "producer_service": "cmev-worker-ocr"}
    result, engine = _run(provenance=real)
    assert result.reasons[0].code == "fixture_provenance_mismatch" and engine.calls == 0


def test_retry_reuses_page_box_and_artifact_identity():
    first, _ = _run()
    second, _ = _run()
    assert first.pages[0].page_id == second.pages[0].page_id == page_id_for(FILE_ID, 1)
    assert [b.box_id for b in first.pages[0].text_boxes] == [b.box_id for b in second.pages[0].text_boxes]
    assert [(a.key, a.sha256, a.artifact_id) for a in first.artifacts] == \
           [(a.key, a.sha256, a.artifact_id) for a in second.artifacts]
    assert first.pages[0] == second.pages[0]


def test_missing_confidence_is_null_with_reason_and_page_partial():
    result, _ = _run(_regions(confidence=None))
    [page] = result.pages
    assert all(b.confidence is None and b.confidence_reason == "engine_supplies_no_confidence"
               for b in page.text_boxes)
    assert page.mean_text_confidence is None and page.mean_text_confidence_reason == "engine_supplies_no_confidence"
    assert page.quality.state == "partial" and "confidence_unavailable" in page.quality.reasons


def test_low_confidence_is_flagged_passed_through_and_gated_by_fraction():
    regions = _regions()
    low = [OcrRegion(r.quad, r.text, 0.41, r.level) if i < 2 else r for i, r in enumerate(regions)]
    result, _ = _run(low)
    [page] = result.pages
    flagged = [b for b in page.text_boxes if "low_confidence_region" in b.flags]
    assert len(flagged) == 2 and {b.confidence for b in flagged} == {0.41}  # unchanged, never corrected
    assert page.quality.state == "complete" and "low_confidence_region" in page.quality.reasons
    many = [OcrRegion(r.quad, r.text, 0.3, r.level) if i < len(regions) // 4 else r for i, r in enumerate(regions)]
    [page] = _run(many)[0].pages
    assert page.quality.state == "partial" and "low_confidence_fraction" in page.quality.reasons


def test_versions_are_kept_verbatim_and_extended_with_what_ran():
    result, _ = _run(versions={"code": "abc", "taxonomy": "t1", "ocr_engine": "stub-ocr"})
    versions = result.pages[0].versions
    assert versions["code"] == "abc" and versions["taxonomy"] == "t1" and versions["ocr_engine"] == "stub-ocr"
    assert versions["ocr_version"] == "0.0.0" and versions["page_config"] == CFG.config_version
    assert versions["m4_code"].startswith("m4-text-layout/")
    assert result.pages[0].provenance.source_kind == "fixture"


def test_artifacts_are_returned_not_written_and_match_their_refs():
    result, _ = _run(config=m4_config(page={"write_debug": True}))
    [page] = result.pages
    by_name = {a.key.rsplit("/", 1)[1]: a for a in result.artifacts}
    assert set(by_name) == {"render.png", "rectified.png", "page_reading.json", "boundary_debug.png"}
    for artifact in result.artifacts:
        assert artifact.key.startswith(f"claims/{page.claim_id}/1/pages/{page.page_id}/")
        assert artifact.object_uri == "s3://cmev-evidence/" + artifact.key
        assert hashlib.sha256(artifact.data).hexdigest() == artifact.sha256 and len(artifact.data) == artifact.byte_count
    assert by_name["rectified.png"].data == by_name["render.png"].data  # no correction: a copy, recorded as such
    reading = json.loads(by_name["page_reading.json"].data)
    assert reading["rectified"]["copy_of_render"] is True
    assert page.page_reading_ref.sha256 == by_name["page_reading.json"].sha256
    assert page.corrected_render_ref.sha256 == by_name["rectified.png"].sha256
    assert page.source_render_ref.sha256 == by_name["render.png"].sha256


def test_unrendered_pages_are_reported_not_dropped():
    data = pdf_bytes([PAGE.image], dpi=150)
    result, _ = _run(data=data, media_type="application/pdf", page_numbers=(1, 4))
    assert result.processing_status == "partial"
    assert [p.page_number for p in result.pages] == [1]
    [missing] = result.unrendered_pages()
    assert (missing["page_number"], missing["page_status"]) == (4, "unreadable")
    assert missing["reasons"][0]["code"] == "page_not_found"


def test_reading_order_bands_then_left_to_right():
    quads = [np.array([[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]], dtype=float)
             for x, y in [(500, 105), (100, 100), (300, 300), (100, 310), (700, 98)]]
    assert reading_order(quads, 18) == [1, 0, 4, 3, 2]


def test_event_payload_validates_against_the_topic_schema():
    result, _ = _run()
    message = load_example("cmev.evt.page-read.v1")
    message["payload"] = page_read_event_payload(result.pages[0])
    validate_message("cmev.evt.page-read.v1", message)
    assert message["payload"]["token_count"] == len(LINES)
    assert message["payload"]["correction_applied"] is False
