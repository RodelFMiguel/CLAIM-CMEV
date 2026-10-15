"""OCR producer ownership and startup pins without loading native OCR libraries."""
from types import SimpleNamespace

import pytest

from claim_cmev import worker
from claim_cmev.documents.text_layout.config import load_page_reading_config
from claim_cmev.documents.text_layout.model_config import load_ocr_model_config
from claim_cmev.orchestration.plan import VersionBundle, real_stage_versions
from claim_cmev.orchestration.services import RuntimeSettings, build_runtimes


def test_ocr_selection_defaults_to_fixture_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("CMEV_OCR_PRODUCER", raising=False)
    assert RuntimeSettings.from_env().ocr_producer == "fixture"
    monkeypatch.setenv("CMEV_OCR_PRODUCER", "real")
    assert RuntimeSettings.from_env().ocr_producer == "real"
    monkeypatch.setenv("CMEV_OCR_PRODUCER", "both")
    with pytest.raises(RuntimeError, match="CMEV_OCR_PRODUCER"):
        RuntimeSettings.from_env()


def test_ocr_pins_only_replace_page_stage_and_match_loaded_config():
    fixture = VersionBundle.fixture()
    selected = VersionBundle.for_runtime(ocr_producer="real")
    cfg = load_page_reading_config()
    pins = selected.for_stage("page_read")
    assert pins == real_stage_versions("page_read") == load_ocr_model_config().stage_versions("0.2.0")
    assert pins["ocr"] == "paddleocr/2.10.0"
    assert pins["preprocess_config"] == cfg.config_version
    assert pins["page_config_sha256"] == cfg.sha256
    for stage in fixture.stages:
        if stage != "page_read":
            assert selected.for_stage(stage) == fixture.for_stage(stage)


def test_ocr_switch_removes_only_fixture_page_reader():
    options = dict(versions=VersionBundle.fixture(), consolidator=None, profile="lean")
    original = {rt.group for rt in build_runtimes(None, "producers", **options)}
    selected = {rt.group for rt in build_runtimes(None, "producers", ocr_producer="real", **options)}
    assert original - selected == {"cmev-worker-ocr"}
    assert selected <= original


def test_ocr_role_requires_handler_and_labels_live_measurements_real():
    with pytest.raises(ValueError, match="page_read_handler"):
        build_runtimes(None, "ocr", profile="full")
    handler = lambda ctx: {}
    runtimes = build_runtimes(None, "ocr", profile="full", source_kind="fixture", page_read_handler=handler)
    assert len(runtimes) == 1
    rt = runtimes[0]
    assert rt.group == "cmev-worker-ocr"
    assert rt.source_kind == "real"
    assert rt.topics == ("cmev.cmd.page-read.v1",)
    assert rt.handlers[rt.topics[0]] is handler


def test_ocr_startup_requires_matching_producer_ownership(monkeypatch):
    monkeypatch.setattr(worker, "check_head", lambda _: "test-head")
    database = SimpleNamespace(engine=None)
    settings = RuntimeSettings("lean", True, None, None)
    with pytest.raises(RuntimeError, match="CMEV_OCR_PRODUCER"):
        worker.startup_checks(database, settings, "ocr", None)
    settings = RuntimeSettings("lean", True, None, None, ocr_producer="real")
    assert worker.startup_checks(database, settings, "ocr", None)["schema_head"] == "test-head"


def test_ocr_startup_checks_registry_before_loading_native_engine(monkeypatch):
    from claim_cmev.documents.text_layout import model_config, paddle
    calls = []
    monkeypatch.setattr(model_config, "verify_ocr_registry", lambda _: (_ for _ in ()).throw(ValueError("corrupt weights")))
    monkeypatch.setattr(paddle.PaddleOcrEngine, "from_config", lambda *_: calls.append("native_load"))
    with pytest.raises(ValueError, match="corrupt weights"):
        worker.load_page_read_worker()
    assert calls == []
