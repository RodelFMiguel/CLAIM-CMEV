"""The M4 output cache: build with the real adapter, verify, resume and load without Paddle.

Every page here is SYNTHETIC test material read by ``StubOcrEngine``, so the records are
labelled ``fixture``. These tests check the cache mechanics, not OCR quality.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from claim_cmev.documents.text_layout import StubOcrEngine, run_page_reading
from claim_cmev.documents.text_layout.synthetic import (
    camera_corners,
    photograph,
    render_estimate_page,
    rotate_within_frame,
)
from claim_cmev.vision.transforms import quad_to_box
from m4_support import line_quads, m4_config, png_bytes, regions_in_rectified, request_for
from pipelines.documents import m4_cache
from pipelines.documents.m4_cache import (
    CACHE_SCHEMA,
    M4CacheError,
    build_m4_cache,
    claim_id_for,
    corrected_boxes_to_norm,
    load_m4_cache,
    source_to_corrected_boxes,
    source_to_corrected_points,
)

CFG = m4_config()
PAGE = render_estimate_page(1240)
LINES = line_quads(PAGE, roles=("column_header", "cell", "total"))
VERSIONS = {"code": "test", "taxonomy": "none"}
DATASET_NAME = "tiny_synthetic_claims"
IDS = ("SYN-00001_P1", "SYN-00001_P2", "SYN-00002_P1", "SYN-00002_P2")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jpeg(image_bgr: np.ndarray) -> bytes:
    buffer = BytesIO()
    Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)).save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def _dataset(tmp_path: Path, images: dict[str, bytes] | None = None) -> Path:
    """Two claims of two pages each, in the layout of the 500-page dataset."""
    root = tmp_path / DATASET_NAME
    flat = _jpeg(PAGE.image)
    rows, annotations = [], []
    for index, image_id in enumerate(IDS):
        data = (images or {}).get(image_id, flat)
        claim, number = image_id.split("_P")
        split = "train" if claim == "SYN-00001" else "validation"
        file_name = f"images/{split}/{image_id}.jpg"
        (root / file_name).parent.mkdir(parents=True, exist_ok=True)
        (root / file_name).write_bytes(data)
        rows.append({"image_id": image_id, "claim_id": claim, "split": split, "file_name": file_name,
                     "width": PAGE.width, "height": PAGE.height, "source_sha256": "0" * 64,
                     "output_sha256": _sha(data), "page_seed": index})
        annotations.append({"image_id": image_id, "claim_id": claim, "page_number": int(number),
                            "page_type": "survey_assessment" if number == "1" else "repair_estimate",
                            "split": split, "file_name": file_name})
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "annotations.jsonl").write_text("".join(json.dumps(a) + "\n" for a in annotations), encoding="utf-8")
    (root / "provenance.json").write_text(json.dumps({"provenance": "fully_synthetic"}), encoding="utf-8")
    return root


def _engine(**kwargs) -> StubOcrEngine:
    return StubOcrEngine(regions_in_rectified(LINES, np.eye(3)), **kwargs)


def _build(dataset: Path, cache: Path, engine: StubOcrEngine | None = None, **kwargs) -> dict:
    return build_m4_cache(dataset, cache, engine=engine or _engine(), config=CFG, versions=VERSIONS,
                          source_kind="fixture", **kwargs)


def test_build_then_load_round_trip(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    manifest = _build(dataset, cache_dir)
    assert manifest["schema"] == CACHE_SCHEMA and manifest["complete"] is True
    assert manifest["dataset"]["name"] == DATASET_NAME and manifest["dataset"]["page_count"] == 4
    assert manifest["dataset"]["manifest_sha256"] == _sha((dataset / "manifest.csv").read_bytes())
    assert manifest["counts"]["pages"] == 4
    assert manifest["counts"]["by_split"] == {"train": 2, "validation": 2}
    assert manifest["counts"]["by_page_type"] == {"repair_estimate": 2, "survey_assessment": 2}
    assert manifest["counts"]["by_page_status"] == {"complete": 4}
    assert manifest["counts"]["by_geometry_correction"] == {"none": 4}
    assert manifest["counts"]["text_boxes"] == 4 * len(LINES)
    assert manifest["m4"]["page_config_sha256"] == CFG.sha256
    assert json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8")) == manifest

    cache = load_m4_cache(cache_dir, dataset, verify="hashes")
    assert [p.image_id for p in cache.pages] == list(IDS)  # dataset manifest order
    assert [p.image_id for p in cache.split("validation")] == list(IDS[2:])
    page = cache.by_id("SYN-00001_P2")
    assert (page.dataset_claim_id, page.split, page.page_type, page.page_number) == \
           ("SYN-00001", "train", "repair_estimate", 2)
    assert (page.processing_status, page.page_status) == ("succeeded", "complete")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", page.claim_id)
    assert page.claim_id == claim_id_for(DATASET_NAME, "SYN-00001") == page.page.claim_id
    assert cache.by_id("SYN-00002_P1").claim_id != page.claim_id  # one claim id per dataset claim
    assert page.page.versions == manifest["m4"]["versions"]
    assert page.page.provenance.producer_service == "m4-cache-builder"
    assert page.page.provenance.source_dataset_id == DATASET_NAME
    assert sorted(b.text for b in page.page.text_boxes) == sorted(t for t, _ in LINES)
    assert page.source_path == dataset / "images/train/SYN-00001_P2.jpg"
    assert page.rectified_path == cache_dir / "pages/SYN-00001_P2/rectified.png"
    assert _sha(page.rectified_path.read_bytes()) == page.page.corrected_render_ref.sha256
    assert _sha((cache_dir / page.entry["page_reading"]).read_bytes()) == page.page.page_reading_ref.sha256
    # no geometry correction: the render is the same bytes, recorded, not stored twice
    assert page.entry["render_same_as_rectified"] is True and page.entry["render"] is None
    assert not (cache_dir / "pages/SYN-00001_P2/render.png").exists()
    assert load_m4_cache(cache_dir).by_id("SYN-00001_P2").source_path is None
    with pytest.raises(KeyError):
        cache.by_id("SYN-99999_P1")


def test_resume_skips_verified_pages_and_rebuilds_a_damaged_one(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    engine = _engine()
    first = _build(dataset, cache_dir, engine)
    assert engine.calls == 4
    second = _build(dataset, cache_dir, engine)
    assert engine.calls == 4  # every page verified on disk: nothing was read again
    assert second["pages"] == first["pages"]
    (cache_dir / "pages/SYN-00002_P1/rectified.png").unlink()
    third = _build(dataset, cache_dir, engine)
    assert engine.calls == 5 and third["counts"]["pages"] == 4
    load_m4_cache(cache_dir, verify="hashes")


def test_limit_and_image_ids_select_pages_and_keep_earlier_ones(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    engine = _engine()
    partial = _build(dataset, cache_dir, engine, limit=2)
    assert [e["image_id"] for e in partial["pages"]] == list(IDS[:2]) and partial["complete"] is False
    full = _build(dataset, cache_dir, engine, image_ids=["SYN-00002_P2"])
    assert [e["image_id"] for e in full["pages"]] == [IDS[0], IDS[1], IDS[3]] and engine.calls == 3
    with pytest.raises(M4CacheError, match="SYN-12345_P9"):
        _build(dataset, cache_dir, engine, image_ids=["SYN-12345_P9"])


def test_a_corrected_page_keeps_its_render_beside_the_corrected_image(tmp_path):
    skewed, _ = rotate_within_frame(PAGE, 4.0)
    dataset, cache_dir = _dataset(tmp_path, {"SYN-00001_P2": _jpeg(skewed)}), tmp_path / "cache"
    manifest = _build(dataset, cache_dir, image_ids=["SYN-00001_P2"])
    [entry] = manifest["pages"]
    assert entry["geometry_correction"] == "rotation" and manifest["counts"]["by_geometry_correction"] == {"rotation": 1}
    assert entry["render_same_as_rectified"] is False and entry["render"] == "pages/SYN-00001_P2/render.png"
    page = load_m4_cache(cache_dir, dataset, verify="hashes").by_id("SYN-00001_P2")
    transform = page.page.transform
    assert (entry["corrected_width"], entry["corrected_height"]) == (transform.corrected_width, transform.corrected_height)
    assert (transform.corrected_width, transform.corrected_height) != (transform.source_width, transform.source_height)
    assert _sha((cache_dir / entry["render"]).read_bytes()) == page.page.source_render_ref.sha256
    assert entry["render_sha256"] != entry["rectified_sha256"] == page.page.corrected_render_ref.sha256
    (cache_dir / entry["render"]).write_bytes(b"not the render")
    with pytest.raises(M4CacheError, match="SYN-00001_P2"):
        load_m4_cache(cache_dir, verify="hashes")


def stub_engine_factory() -> StubOcrEngine:
    """Module level, so a spawned worker process can import it."""
    return _engine()


def test_worker_processes_build_their_own_engine_and_write_the_same_pages(tmp_path):
    dataset = _dataset(tmp_path)
    one = _build(dataset, tmp_path / "one")
    many = build_m4_cache(dataset, tmp_path / "many", engine_factory=stub_engine_factory, workers=2, config=CFG,
                          versions=VERSIONS, source_kind="fixture")
    timings = {"ocr_ms", "geometry_ms", "total_ms"}

    def untimed(manifest: dict) -> list[dict]:
        return [{k: v for k, v in entry.items() if k not in timings} for entry in manifest["pages"]]

    assert untimed(many) == untimed(one) and many["last_run"]["workers"] == 2
    load_m4_cache(tmp_path / "many", dataset, verify="hashes")
    with pytest.raises(ValueError, match="engine_factory"):
        build_m4_cache(dataset, tmp_path / "none", engine=_engine(), workers=2, config=CFG, versions=VERSIONS,
                       source_kind="fixture")


def test_a_tampered_file_fails_hash_verification_naming_the_page(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    _build(dataset, cache_dir)
    target = cache_dir / "pages/SYN-00002_P2/rectified.png"
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(M4CacheError, match="SYN-00002_P2"):
        load_m4_cache(cache_dir, verify="hashes")
    assert len(load_m4_cache(cache_dir, verify="none").pages) == 4  # parse only
    target.unlink()
    with pytest.raises(M4CacheError, match="SYN-00002_P2"):
        load_m4_cache(cache_dir, verify="hashes")
    with pytest.raises(ValueError, match="verify"):
        load_m4_cache(cache_dir, verify="sometimes")


def test_a_source_image_that_differs_from_the_manifest_is_refused(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    changed = dataset / "images/train/SYN-00001_P2.jpg"
    changed.write_bytes(changed.read_bytes() + b"\x00")
    engine = _engine()
    manifest = _build(dataset, cache_dir, engine)
    assert engine.calls == 3  # the changed page never reached OCR
    entry = {e["image_id"]: e for e in manifest["pages"]}["SYN-00001_P2"]
    assert (entry["processing_status"], entry["page_status"]) == ("failed", "unreadable")
    assert entry["reasons"] == ["artifact_hash_mismatch"]
    assert entry["document_page"] is None and entry["rectified"] is None and entry["box_count"] == 0
    assert not (cache_dir / "pages/SYN-00001_P2/rectified.png").exists()
    assert manifest["counts"]["by_page_status"] == {"complete": 3, "unreadable": 1}
    page = load_m4_cache(cache_dir, verify="hashes").by_id("SYN-00001_P2")
    assert page.page is None and page.rectified_path is None and page.processing_status == "failed"
    # the loader refuses a dataset whose bytes are not the ones the cache was built from
    with pytest.raises(M4CacheError, match="SYN-00001_P2"):
        load_m4_cache(cache_dir, dataset, verify="hashes")


def test_an_ocr_failure_is_recorded_unreadable_never_an_empty_success(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    manifest = _build(dataset, cache_dir, StubOcrEngine(error=MemoryError("simulated")), limit=1)
    [entry] = manifest["pages"]
    assert (entry["processing_status"], entry["page_status"]) == ("failed", "unreadable")
    assert entry["reasons"] == ["ocr_failed"] and entry["box_count"] == 0
    assert manifest["counts"]["by_page_status"] == {"unreadable": 1} and manifest["counts"]["text_boxes"] == 0
    [page] = load_m4_cache(cache_dir, verify="hashes").pages
    assert page.page is not None and page.page.quality.state == "unreadable" and page.page.text_boxes == []
    # a failed page is read again on the next run; a later success replaces the failure record
    healed = _build(dataset, cache_dir, _engine(), limit=1)
    assert healed["pages"][0]["page_status"] == "complete" and healed["pages"][0]["box_count"] == len(LINES)


def test_pages_built_under_other_versions_are_rebuilt_not_mixed(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    engine = _engine()
    _build(dataset, cache_dir, engine, limit=2)
    manifest = build_m4_cache(dataset, cache_dir, engine=engine, config=CFG, source_kind="fixture",
                              versions={"code": "other", "taxonomy": "none"}, limit=1)
    assert engine.calls == 3  # the selected page was rebuilt under the new versions
    assert [e["image_id"] for e in manifest["pages"]] == [IDS[0]]  # the stale page is left out
    assert manifest["m4"]["versions"]["code"] == "other" and manifest["stale_pages"] == [IDS[1]]


def test_an_unknown_schema_is_rejected(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / "cache"
    _build(dataset, cache_dir, limit=1)
    path = cache_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema"] = "cmev-m4-cache/2"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(M4CacheError, match="cmev-m4-cache/2"):
        load_m4_cache(cache_dir, verify="none")
    with pytest.raises(M4CacheError, match="manifest.json"):
        load_m4_cache(tmp_path / "absent")


def _read(data: bytes, page_to_render: np.ndarray):
    """One page through the real adapter, with truth regions placed in the corrected render."""
    from claim_cmev.documents.text_layout import correct_page_geometry, render_pages

    [rendered] = render_pages(data, "image/png", CFG.page.render_dpi, CFG.page.max_pages_per_job)
    geometry = correct_page_geometry(rendered.image, CFG)
    engine = StubOcrEngine(regions_in_rectified(LINES, geometry.homography @ page_to_render))
    result = run_page_reading(request_for(data, "image/png"), engine, CFG)
    return result.pages[0], result.page_readings[0]


def test_transform_direction_perspective_maps_original_onto_rectified():
    size = (1200, 1600)
    photo, page_to_photo = photograph(PAGE, camera_corners(PAGE, size, pitch_deg=25, yaw_deg=-15, roll_deg=3),
                                      size, seed=1)
    page, reading = _read(png_bytes(photo), page_to_photo)
    assert page.transform.geometry_correction == "perspective"
    assert not np.allclose(page.transform.homography, np.eye(3))
    for box in page.text_boxes:  # EXIF 1: the stored frame is the render frame of quad_original
        mapped = source_to_corrected_points(page, box.quad_original)
        assert np.abs(mapped - np.array(box.quad_rectified)).max() <= 0.01
    # the inverse matrix sends the same points somewhere else entirely: the direction matters
    wrong = np.array([page.transform.corrected_to_source(x, y) for x, y in page.text_boxes[0].quad_original])
    assert np.abs(wrong - np.array(page.text_boxes[0].quad_rectified)).max() > 50
    boxes = np.array([quad_to_box(np.array(b.quad_original)) for b in page.text_boxes])
    mapped = source_to_corrected_boxes(page, boxes)
    assert mapped.shape == (len(boxes), 4) and mapped.dtype == np.float64
    for (bx0, by0, bx1, by1), row, box in zip(boxes, mapped, page.text_boxes):
        # the four corners are warped, then bounded: never the two opposite corners alone
        corners = source_to_corrected_points(page, [(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])
        assert np.allclose(row, quad_to_box(corners))
        x0, y0, x1, y1 = quad_to_box(np.array(box.quad_rectified))  # and so they contain the text quad
        assert row[0] <= x0 + 0.01 and row[1] <= y0 + 0.01 and row[2] >= x1 - 0.01 and row[3] >= y1 - 0.01
    assert reading["boxes"][0]["quad_original"] == [list(p) for p in page.text_boxes[0].quad_original]


def test_transform_direction_accounts_for_exif_orientation():
    display, page_to_display = rotate_within_frame(PAGE, 4.0)
    stored = np.ascontiguousarray(np.rot90(display, k=1))  # stored sideways; EXIF 6 displays it upright
    page, reading = _read(png_bytes(stored, exif_orientation=6), page_to_display)
    assert page.transform.geometry_correction == "rotation" and page.transform.exif_orientation == 6
    for box, contract_box in zip(reading["boxes"], page.text_boxes):
        # quad_source is M4's own statement of where the box lies in the stored file pixels
        mapped = source_to_corrected_points(page, box["quad_source"])
        assert np.abs(mapped - np.array(contract_box.quad_rectified)).max() <= 0.01
        # the oriented (render) frame is a different frame: quad_original does not map there
        assert np.abs(source_to_corrected_points(page, box["quad_original"])
                      - np.array(contract_box.quad_rectified)).max() > 50


def test_boxes_on_an_uncorrected_page_are_unchanged_and_normalise_like_m4():
    page, _ = _read(png_bytes(PAGE.image), np.eye(3))
    assert page.transform.geometry_correction == "none"
    boxes = np.array([quad_to_box(np.array(b.quad_rectified)) for b in page.text_boxes])
    assert np.allclose(source_to_corrected_boxes(page, boxes), boxes)
    norm = corrected_boxes_to_norm(page, boxes)
    assert np.abs(norm - np.array([b.box_norm for b in page.text_boxes])).max() <= 1e-6
    outside = corrected_boxes_to_norm(page, [[-10.0, -5.0, page.transform.corrected_width + 30.0, 20.0]])
    assert outside[0, 0] == 0.0 and outside[0, 1] == 0.0 and outside[0, 2] == 1.0
    assert source_to_corrected_boxes(page, np.zeros((0, 4))).shape == (0, 4)
    with pytest.raises(ValueError):
        source_to_corrected_boxes(page, [[1.0, 2.0, 3.0]])


def test_the_loader_does_not_import_paddle_or_opencv():
    import subprocess
    import sys

    code = ("import sys; import pipelines.documents.m4_cache as m; "
            "bad = [n for n in ('paddle', 'paddleocr', 'cv2') if n in sys.modules]; "
            "assert not bad, bad; print(m.CACHE_SCHEMA)")
    repo = Path(m4_cache.__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True, text=True,
                          env={"PYTHONPATH": f"{repo}{':'}{repo / 'src'}", "PATH": ""})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == CACHE_SCHEMA


def test_loading_selected_splits_never_opens_excluded_page_files(tmp_path):
    dataset, cache_dir = _dataset(tmp_path), tmp_path / 'cache'
    manifest = _build(dataset, cache_dir)
    # An excluded page can be absent: filtering must precede hashing and deserialisation.
    for entry in manifest['pages']:
        if entry['split'] == 'validation':
            (cache_dir / entry['document_page']).unlink()
    cache = load_m4_cache(cache_dir, dataset, splits=('train',))
    assert len(cache.pages) == 2
    assert {page.split for page in cache.pages} == {'train'}
