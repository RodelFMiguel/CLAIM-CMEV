"""Training loop, run reload and registry export of the pen-mark detector, on a toy problem.

The pages are a few drawn rectangles, the network starts from RANDOM weights (nothing is
downloaded) and trains for two short epochs on the CPU. The numbers mean nothing: these
tests check the mechanics, namely what a run writes, that it reloads and resumes, and that
an export is verified with the serving loader before it appears.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml
from PIL import Image

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")
pytest.importorskip("safetensors")

from claim_cmev.documents.pen_marks import detector as d  # noqa: E402
from claim_cmev.documents.pen_marks import load_linking_config, load_penmark_detector  # noqa: E402
from pipelines.documents import penmarks_data as pd_  # noqa: E402
from pipelines.documents import penmarks_evaluation as ev  # noqa: E402
from pipelines.documents import penmarks_training as tr  # noqa: E402

W, H = 96, 128
CLAIMS = ["01JAX7Q0VN4Z3K9F2M8R6T1C5A", "01JAX7Q0VN4Z3K9F2M8R6T1C5B", "01JAX7Q0VN4Z3K9F2M8R6T1C5C"]


def _pages(folder: Path, split: str, count: int, offset: int = 0) -> list[pd_.PenMarkPage]:
    pages = []
    for index in range(count):
        number = offset + index
        image_id = f"SYN-{number:02d}_P2"
        marked = index % 2 == 0
        box = (20.0, 30.0 + 4 * index, 70.0, 46.0 + 4 * index)
        pixels = np.full((H, W, 3), 250, dtype=np.uint8)
        if marked:
            pixels[int(box[1]):int(box[3]), int(box[0]):int(box[2])] = (44, 84, 170)
        path = folder / f"{image_id}.png"
        Image.fromarray(pixels).save(path)
        mark = pd_.MarkBox(f"{image_id}-mark-01", "exclusion", "x", box, (box,), "part_1", f"{image_id}:part_1", None)
        row = pd_.RowGeometry("part_1", f"{image_id}:part_1", (10.0, 20.0, 90.0, 60.0), (60.0, 30.0, 85.0, 40.0), "exclusion")
        pages.append(pd_.PenMarkPage(
            image_id=image_id, dataset_claim_id=f"SYN-{number:02d}", claim_id=CLAIMS[number % 3], split=split,
            page_type="repair_estimate", page_number=2, page_id=f"pg-{image_id}", page_status="complete",
            image_path=path, width=W, height=H, writer="Writer 1", marks=(mark,) if marked else (),
            rows=(row,), boxes=np.array([box] if marked else [], dtype=np.float32).reshape(-1, 4),
            labels=np.array([1] if marked else [], dtype=np.int64)))
    return pages


def _config(tmp_path: Path, **changes) -> tr.TrainingConfig:
    values = dict(min_size=96, max_size=128, pretrained=False, batch_size=2, epochs=2, warmup_steps=2, patience=5,
                  device="cpu", augment=pd_.AugmentConfig(blur_probability=0.0, jpeg_probability=0.0),
                  artifacts_root=str(tmp_path / "artifacts"), max_detections_per_page=10, score_floor=0.05)
    values.update(changes)
    return tr.TrainingConfig(**values)


DATA = {"dataset": {"name": "toy"}, "m4_cache": {"manifest_sha256": "0" * 64, "versions": {"m4_code": "test"}},
        "split_files": {"marks_coco_train.json": "1" * 64}, "partitions": {"train": {"pages": 4}}}


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    folder = tmp_path_factory.mktemp("toy")
    train_pages, val_pages = _pages(folder, "train", 4), _pages(folder, "validation", 2, offset=10)
    lines: list[str] = []
    run = tr.train(_config(folder), train_pages, val_pages, data_record=DATA, log=lines.append)
    return folder, train_pages, val_pages, run, lines


# --------------------------------------------------------------------------- configuration and schedule

def test_the_learning_rate_warms_up_linearly_then_steps_down_at_70_and_90_percent():
    config = tr.TrainingConfig(epochs=20, learning_rate=0.005, warmup_steps=100, warmup_factor=0.001)
    rate = lambda step, epoch: tr.learning_rate_at(step, epoch, config)  # noqa: E731
    assert rate(0, 0) == pytest.approx(0.005 * 0.001)
    assert rate(50, 0) == pytest.approx(0.005 * (0.001 + 0.999 * 0.5))
    assert rate(100, 1) == pytest.approx(0.005) and rate(900, 13) == pytest.approx(0.005)
    assert rate(1300, 14) == pytest.approx(0.0005) and rate(1700, 17) == pytest.approx(0.0005)
    assert rate(1800, 18) == pytest.approx(0.00005) and rate(1900, 19) == pytest.approx(0.00005)
    assert tr.decay_epochs(config) == (14, 18)
    assert tr.decay_epochs(tr.TrainingConfig(epochs=5)) == (4, 5), "the 90 % step lands after the last of 5 epochs"


def test_a_configuration_describes_the_served_detector_and_rejects_bad_values(tmp_path):
    config = _config(tmp_path, anchor_sizes=tr.ANCHOR_PRESETS["pen_marks_wide"]["anchor_sizes"],
                     anchor_aspect_ratios=tr.ANCHOR_PRESETS["pen_marks_wide"]["anchor_aspect_ratios"])
    settings = config.detector_settings()
    assert isinstance(settings, d.DetectorSettings) and (settings.min_size, settings.max_size) == (96, 128)
    assert settings.anchors_per_location == 8 and settings.max_detections_per_page == 10
    assert tr.TrainingConfig.from_record(json.loads(json.dumps(config.to_record()))) == config
    assert set(tr.ANCHOR_PRESETS) == {"torchvision_default", "pen_marks_wide"}
    for bad in (dict(batch_size=0), dict(epochs=0), dict(lr_milestones=(0.9, 0.7)), dict(accumulation_steps=0)):
        with pytest.raises(ValueError):
            _config(tmp_path, **bad)


def test_run_ids_are_unique_and_name_the_model():
    first, second = tr.new_run_id(), tr.new_run_id()
    assert first.startswith("penmarks-fasterrcnn-") and first != second


# --------------------------------------------------------------------------- the network

def _reference_state(**_):
    """Stands in for torchvision's COCO weights: default anchors and 91 classes, random values."""
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    model = d.build_model(d.DetectorSettings())
    model.roi_heads.box_predictor = FastRCNNPredictor(1024, 91)
    return model.state_dict()


def test_pretrained_weights_are_transferred_except_the_layers_whose_shape_changed(monkeypatch, tmp_path):
    reference = _reference_state()
    monkeypatch.setattr(tr, "_coco_state", lambda: reference)
    model, info = tr.build_detector(_config(tmp_path, pretrained=True))
    assert info["pretrained"] and sorted(info["new_tensors"]) == [
        "roi_heads.box_predictor.bbox_pred.bias", "roi_heads.box_predictor.bbox_pred.weight",
        "roi_heads.box_predictor.cls_score.bias", "roi_heads.box_predictor.cls_score.weight"]
    assert info["transferred_tensors"] == len(model.state_dict()) - 4
    assert torch.equal(model.state_dict()["backbone.body.layer4.2.conv3.weight"],
                       reference["backbone.body.layer4.2.conv3.weight"])

    wide = _config(tmp_path, pretrained=True, **tr.ANCHOR_PRESETS["pen_marks_wide"])
    model, info = tr.build_detector(wide)
    assert {name.rsplit(".", 1)[0] for name in info["new_tensors"]} == {
        "roi_heads.box_predictor.cls_score", "roi_heads.box_predictor.bbox_pred", "rpn.head.cls_logits",
        "rpn.head.bbox_pred"}
    assert model.rpn.head.cls_logits.out_channels == 8

    _, scratch = tr.build_detector(_config(tmp_path))
    assert not scratch["pretrained"] and scratch["transferred_tensors"] == 0
    rows = tr.parameter_table(model)
    assert [r["block"] for r in rows][:2] == ["backbone.body (ResNet-50)", "backbone.fpn (feature pyramid)"]
    assert sum(r["parameters"] for r in rows) == sum(p.numel() for p in model.parameters())
    assert rows[0]["trainable"] < rows[0]["parameters"], "the first backbone stages stay frozen"


def test_rpn_anchors_cover_every_level_in_the_resized_frame(tmp_path):
    model, _ = tr.build_detector(_config(tmp_path, min_size=96, max_size=160))
    grid = tr.rpn_anchors(model, height=H, width=W)
    assert grid["resized"] == (128, 96) and grid["padded"] == (128, 96) and grid["scale"] == pytest.approx(1.0)
    assert grid["feature_shapes"] == [(32, 24), (16, 12), (8, 6), (4, 3), (2, 2)]
    assert grid["anchors_per_level"] == [32 * 24 * 3, 16 * 12 * 3, 8 * 6 * 3, 4 * 3 * 3, 2 * 2 * 3]
    assert grid["anchors"].shape == (sum(grid["anchors_per_level"]), 4)
    widths = grid["anchors"][:3, 2] - grid["anchors"][:3, 0]
    heights = grid["anchors"][:3, 3] - grid["anchors"][:3, 1]
    np.testing.assert_allclose((heights / widths).numpy(), [0.5, 1.0, 2.0], rtol=0.1)  # 32 px anchors, rounded


# --------------------------------------------------------------------------- training

def test_a_run_writes_its_configuration_history_and_checkpoints(trained):
    folder, _, val_pages, run, lines = trained
    run_dir = Path(run["run_dir"])
    assert run_dir.parent == folder / "artifacts" / "models" / "penmarks" and run_dir.name == run["run_id"]
    assert {p.name for p in run_dir.iterdir()} >= {"config.json", "run.json", "history.jsonl", "best.safetensors", "last.pt"}
    history = [json.loads(line) for line in (run_dir / "history.jsonl").read_text().splitlines()]
    assert [row["epoch"] for row in history] == [1, 2]
    for row in history:
        assert set(row["train_loss"]) == set(row["val_loss"]) == {
            "loss_classifier", "loss_box_reg", "loss_objectness", "loss_rpn_box_reg", "total"}
        assert all(math.isfinite(v) for v in row["train_loss"].values()) and row["learning_rate"] > 0
        assert 0.0 <= row["val_selection_score"] <= 1.0 and row["steps"] == 2
    record = json.loads((run_dir / "run.json").read_text())
    assert record["status"] == "completed" and record["completed_epochs"] == 2 and record["best_epoch"] in (1, 2)
    assert record["data"] == DATA and record["seed"] == 20260922 and record["selection_metric"].startswith("mean")
    assert record["environment"]["torch"] == torch.__version__ and record["device"] == "cpu"
    assert record["pages"] == {"train": 4, "validation": 2}
    assert record["page_ids"] == {"train": ["SYN-00_P2", "SYN-01_P2", "SYN-02_P2", "SYN-03_P2"],
                                  "validation": ["SYN-10_P2", "SYN-11_P2"]}, "a run names the pages it used"
    assert Path(run["evaluation_dir"]) == folder / "artifacts" / "evaluation" / run["run_id"]
    assert any("epoch 1/2" in line for line in lines)


def test_a_saved_run_reloads_and_predicts_without_training(trained):
    _, _, val_pages, run, _ = trained
    model, config, record = tr.load_run(run["run_dir"], device="cpu")
    assert not model.training and config.epochs == 2 and record["run_id"] == run["run_id"]
    predictions = tr.predict_pages(model, val_pages, device="cpu")
    assert set(predictions) == {p.image_id for p in val_pages}
    for prediction in predictions.values():
        assert isinstance(prediction, ev.PagePrediction) and prediction.boxes.shape == (len(prediction.scores), 4)
        assert list(prediction.scores) == sorted(prediction.scores, reverse=True)
    again = tr.predict_pages(tr.load_run(run["run_dir"], device="cpu")[0], val_pages, device="cpu", batch_size=1)
    for image_id, prediction in predictions.items():
        np.testing.assert_allclose(again[image_id].boxes, prediction.boxes, atol=1e-3)
        np.testing.assert_allclose(again[image_id].scores, prediction.scores, atol=1e-5)


def test_an_interrupted_run_resumes_from_its_last_epoch(trained, tmp_path):
    _, train_pages, val_pages, run, _ = trained
    copy = tmp_path / "artifacts" / "models" / "penmarks" / run["run_id"]  # the shared run stays as it is
    shutil.copytree(run["run_dir"], copy)
    lines: list[str] = []
    longer = _config(tmp_path, epochs=3, run_id=run["run_id"])
    resumed = tr.train(longer, train_pages, val_pages, data_record=DATA, resume_from=copy, log=lines.append)
    history = [json.loads(line) for line in (copy / "history.jsonl").read_text().splitlines()]
    assert [row["epoch"] for row in history] == [1, 2, 3] and resumed["completed_epochs"] == 3
    assert sum("epoch " in line and "/3" in line for line in lines) == 1, "only the missing epoch was trained"
    assert any("resuming" in line for line in lines)
    with pytest.raises(ValueError, match="differs"):
        tr.train(_config(tmp_path, epochs=3, min_size=64, run_id=run["run_id"]), train_pages, val_pages,
                 data_record=DATA, resume_from=copy)
    with pytest.raises(FileExistsError, match="never overwritten"):
        tr.train(_config(tmp_path, run_id=run["run_id"]), train_pages, val_pages, data_record=DATA)


def test_training_stops_after_the_configured_evaluations_without_improvement(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "selection_score", lambda pages, predictions: 0.25)
    pages = _pages(tmp_path, "train", 2)
    run = tr.train(_config(tmp_path, epochs=6, patience=2), pages, pages[:1], data_record=DATA, log=lambda _: None)
    assert (run["status"], run["completed_epochs"], run["best_epoch"]) == ("stopped_early", 3, 1)


def test_a_non_finite_loss_stops_the_run_with_an_error(tmp_path, monkeypatch):
    pages = _pages(tmp_path, "train", 2)
    monkeypatch.setattr(tr, "learning_rate_at", lambda *args, **kwargs: float("inf"))
    with pytest.raises(FloatingPointError, match="loss"):
        tr.train(_config(tmp_path, epochs=2), pages, pages[:1], data_record=DATA, log=lambda _: None)


# --------------------------------------------------------------------------- export

def _linking(recall):
    return {"detector": {"row_level": {"exclusion": {"precision": None, "recall": recall}}, "conflicting_rows": 0},
            "oracle_detections": {"row_level": {"exclusion": {"precision": 1.0, "recall": 1.0}}}}


REPORT = {"splits": ["validation"], "pages": {"total": 2}, "marks": {"exclusion": 1, "price_change": 0},
          "detection": {"mean_ap50": 0.0}, "operating_point": {"classes": {}},
          "linking": {"primary_rows": "oracle_cell_union_rows", "oracle_cell_union_rows": _linking(0.5),
                      "oracle_table_band_rows": _linking(0.25)}}
THRESHOLDS = {"exclusion": 0.5, "price_change": 0.45}
ALTERNATIVES = {"f1_rule": {"score_threshold": {"exclusion": 0.9, "price_change": 0.59}},
                "configured": {"score_threshold": {"exclusion": 0.5, "price_change": 0.45}}}


def _export(trained, registry: Path, name: str = "toy-serving", **kwargs) -> Path:
    _, _, val_pages, run, _ = trained
    return tr.export_for_serving(run["run_dir"], name, registry_root=registry, evaluation_report=REPORT,
                                 thresholds=THRESHOLDS, check_pages=val_pages, created_by="unit test", **kwargs)


def test_an_export_is_a_complete_candidate_entry_the_serving_loader_accepts(trained, tmp_path):
    _, _, val_pages, run, _ = trained
    entry = _export(trained, tmp_path / "registry")
    assert entry == tmp_path / "registry" / "penmarks" / "toy-serving"
    assert {p.name for p in entry.iterdir()} == {
        "model.safetensors", "manifest.json", "label_schema.json", "preprocessing.json", "split_manifest.json",
        "evaluation_report.json", "training_log.jsonl", "config.yaml", "NOTICE.md", "serving_check.json"}
    manifest = json.loads((entry / "manifest.json").read_text())
    assert (manifest["model_id"], manifest["version"], manifest["status"]) == ("penmarks", "penmarks/toy-serving", "candidate")
    assert manifest["task"] == "object_detection" and manifest["architecture"] == "fasterrcnn_resnet50_fpn"
    assert manifest["provenance"] == "synthetic" and manifest["seed"] == 20260922
    for name in tr.MANIFEST_FIELDS:
        assert name in manifest, name
        assert manifest[name] is not None or manifest.get(f"{name}_reason"), f"{name} is null without a reason"
    assert manifest["base_checkpoint"] is None and "random" in manifest["base_checkpoint_reason"]
    assert manifest["thresholds"]["score_threshold"] == THRESHOLDS and manifest["thresholds"]["status"] == "proposed"
    assert manifest["m4_cache"] == DATA["m4_cache"] and manifest["source_run"]["run_id"] == run["run_id"]
    assert all(target["status"] == "not_evaluated" for target in manifest["targets"])
    assert json.loads((entry / "evaluation_report.json").read_text())["validation"] == REPORT
    assert yaml.safe_load((entry / "config.yaml").read_text())["min_size"] == 96
    # Linking metrics carry the row definition in their name; the table bands are a contrast, M5 rows optional.
    metrics = manifest["metrics"]
    assert metrics["row_level_with_oracle_cell_union_rows"] == {"exclusion": {"precision": None, "recall": 0.5}}
    assert metrics["row_level_with_oracle_table_band_rows"]["exclusion"]["recall"] == 0.25
    assert metrics["row_level_with_m5_rows"] is None and "M5 rows" in metrics["row_level_with_m5_rows_reason"]
    assert "row_level_with_oracle_rows" not in metrics and "contrast" in metrics["row_level_with_oracle_table_band_rows_note"]
    assert "union" in manifest["known_limits"] and "optimistic" in manifest["known_limits"]
    assert manifest["quick_run"] is False
    split = json.loads((entry / "split_manifest.json").read_text())
    assert split["pages_used"] == {"train": ["SYN-00_P2", "SYN-01_P2", "SYN-02_P2", "SYN-03_P2"],
                                   "validation": ["SYN-10_P2", "SYN-11_P2"]}
    assert split["ratios"] == {"train": 1.0} and "pages" in split["ratios_basis"]
    log = (entry / "training_log.jsonl").read_text().splitlines()
    assert json.loads(log[0])["epoch"] == 1 and "effective_config" in json.loads(log[-1])
    assert "BSD-3-Clause" in (entry / "NOTICE.md").read_text() and "synthetic" in (entry / "NOTICE.md").read_text()

    detector = load_penmark_detector(entry)
    assert detector.model_version == "penmarks/toy-serving" and detector.settings == tr.load_run(run["run_dir"])[1].detector_settings()
    check = json.loads((entry / "serving_check.json").read_text())
    assert check["pages"] == 2 and check["identical_labels"] and check["max_box_difference_px"] <= check["box_tolerance_px"]
    # This toy run did not start from COCO: its reference is built by the serving builder, which is the weaker check.
    assert check["reference"] == "serving_builder" and "weaker" in check["reference_note"]
    assert check["history_check"]["ran"] and check["history_check"]["passed"]
    assert check["history_check"]["epoch"] == run["best_epoch"] and check["history_check"]["max_difference"] == 0.0
    page = val_pages[0]
    records = detector.detect(pd_.load_image(page), page_id=page.page_id)
    selection = tr.serving_selection(entry)
    assert selection["model"]["model_version"] == "penmarks/toy-serving"
    assert selection["linking"]["detection"]["score_threshold"] == THRESHOLDS
    assert isinstance(records, list)


def test_an_existing_entry_is_never_replaced_and_a_failed_check_leaves_nothing_behind(trained, tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    _export(trained, registry)
    with pytest.raises(FileExistsError, match="not replaced"):
        _export(trained, registry)
    with pytest.raises(ValueError, match="name"):
        _export(trained, registry, name="nested/name")

    monkeypatch.setattr(tr, "BOX_TOLERANCE_PX", -1.0)  # no export can pass
    with pytest.raises(RuntimeError, match="nothing was exported"):
        _export(trained, registry, name="second")
    assert sorted(p.name for p in (registry / "penmarks").iterdir()) == ["toy-serving"]


def test_selected_thresholds_and_report_are_required_for_an_export(trained, tmp_path):
    _, _, val_pages, run, _ = trained
    with pytest.raises(ValueError, match="check_pages"):
        tr.export_for_serving(run["run_dir"], "x", registry_root=tmp_path, evaluation_report=REPORT,
                              thresholds=THRESHOLDS, check_pages=[], created_by="unit test")
    with pytest.raises(ValueError, match="thresholds"):
        tr.export_for_serving(run["run_dir"], "x", registry_root=tmp_path, evaluation_report=REPORT,
                              thresholds={"exclusion": 0.5}, check_pages=val_pages, created_by="unit test")


def test_the_proposed_thresholds_travel_with_their_alternative_and_no_value_is_singled_out(trained, tmp_path):
    entry = _export(trained, tmp_path / "registry", threshold_alternatives=ALTERNATIVES)
    thresholds = json.loads((entry / "manifest.json").read_text())["thresholds"]
    assert thresholds["score_threshold"] == THRESHOLDS and thresholds["alternatives"] == ALTERNATIVES
    assert thresholds["status"] == "proposed" and "team" in thresholds["note"] and "optimistic" in thresholds["note"]
    selection = tr.serving_selection(entry)
    assert selection["linking_alternatives"] == ALTERNATIVES
    plain = json.loads((_export(trained, tmp_path / "registry", name="plain") / "manifest.json").read_text())
    assert plain["thresholds"]["alternatives"] is None


def test_a_quick_or_subset_export_says_so_and_lists_the_pages_it_used(trained, tmp_path):
    _, train_pages, val_pages, run, _ = trained
    entry = _export(trained, tmp_path / "registry", quick_run=True)
    manifest = json.loads((entry / "manifest.json").read_text())
    assert manifest["quick_run"] is True and manifest["known_limits"].startswith("QUICK RUN")
    assert "not a model result" in manifest["quick_run_note"] and "QUICK RUN" in (entry / "NOTICE.md").read_text()
    assert manifest["status"] == "candidate"

    # A run that used fewer pages than its partition holds is marked the same way, whatever the caller says.
    copy = tmp_path / "artifacts" / "models" / "penmarks" / run["run_id"]
    shutil.copytree(run["run_dir"], copy)
    record = json.loads((copy / "run.json").read_text())
    record["data"]["partitions"] = {"train": {"pages": 350}, "validation": {"pages": 76}, "test": {"pages": 74}}
    (copy / "run.json").write_text(json.dumps(record))
    entry = tr.export_for_serving(copy, "subset", registry_root=tmp_path / "registry", evaluation_report=REPORT,
                                  thresholds=THRESHOLDS, check_pages=val_pages, created_by="unit test")
    manifest = json.loads((entry / "manifest.json").read_text())
    split = json.loads((entry / "split_manifest.json").read_text())
    assert manifest["quick_run"] is True and "4 of 350" in manifest["quick_run_note"]
    assert split["ratios"] == {"train": 0.7, "validation": 0.152, "test": 0.148}
    assert len(split["pages_used"]["train"]) == 4 and split["pages_used_counts"] == {"train": 4, "validation": 2}


def test_pages_used_come_from_the_run_record_or_from_whole_partitions_only(trained):
    _, train_pages, val_pages, run, _ = trained
    record = json.loads((Path(run["run_dir"]) / "run.json").read_text())
    everything = {"train": train_pages, "validation": val_pages}
    assert tr.pages_used(record, everything) == record["page_ids"]
    older = {key: value for key, value in record.items() if key != "page_ids"}   # a run saved before page_ids existed
    assert tr.pages_used(older, everything) == record["page_ids"]
    with pytest.raises(ValueError, match="4 train pages"):
        tr.pages_used(older, {"train": train_pages[:2], "validation": val_pages})


def test_artifacts_root_is_recorded_relative_to_the_repository_when_inside_it(trained, tmp_path, monkeypatch):
    folder, _, _, run, _ = trained
    outside = _export(trained, tmp_path / "registry")
    assert yaml.safe_load((outside / "config.yaml").read_text())["artifacts_root"] == str(folder / "artifacts")
    monkeypatch.setattr(tr, "_REPO", folder)   # now the run lies inside "the repository"
    inside = _export(trained, tmp_path / "registry", name="inside")
    assert yaml.safe_load((inside / "config.yaml").read_text())["artifacts_root"] == "artifacts"
    assert json.loads((inside / "training_log.jsonl").read_text().splitlines()[-1])["effective_config"]["artifacts_root"] == "artifacts"


def test_the_reference_of_the_serving_check_is_independent_of_the_serving_builder_when_it_can_be(trained, tmp_path,
                                                                                                 monkeypatch):
    _, _, val_pages, run, _ = trained
    config = tr.TrainingConfig()
    coco = {"starting_point": {"base_checkpoint": {"name": "torchvision COCO"}}}
    assert tr.reference_kind(config, coco) == "torchvision_factory"
    assert tr.reference_kind(config, {"starting_point": {"base_checkpoint": None}}) == "serving_builder"
    assert tr.reference_kind(tr.TrainingConfig(**tr.ANCHOR_PRESETS["pen_marks_wide"]), coco) == "serving_builder"

    # Pretend the toy run started from COCO, and stand in for torchvision's factory (no download in a unit test).
    copy = tmp_path / "artifacts" / "models" / "penmarks" / run["run_id"]
    shutil.copytree(run["run_dir"], copy)
    record = json.loads((copy / "run.json").read_text())
    record["starting_point"]["base_checkpoint"] = {"name": "toy", "file": "toy.pth", "sha256": "0" * 64, "url": "none"}
    (copy / "run.json").write_text(json.dumps(record))
    built = []

    def factory(config, weights_path, *, faithful=True):
        settings = config.detector_settings()
        model = d.build_model(settings if faithful else settings.model_copy(update={"min_size": 64, "max_size": 96}))
        model.load_state_dict(tr.load_file(str(weights_path)), strict=True)
        built.append(faithful)
        return model

    monkeypatch.setattr(tr, "_torchvision_factory_model", factory)
    export = lambda name: tr.export_for_serving(copy, name, registry_root=tmp_path / "registry",  # noqa: E731
                                                evaluation_report=REPORT, thresholds=THRESHOLDS,
                                                check_pages=val_pages, created_by="unit test")
    check = json.loads((export("independent") / "serving_check.json").read_text())
    assert built == [True] and check["reference"] == "torchvision_factory" and check["passed"]
    assert "independent" in check["reference_note"]
    model, info = tr.reference_model(copy)
    assert info["reference"] == "torchvision_factory" and not model.training

    # A reference that is built differently from the served network does not agree with it: nothing is exported.
    monkeypatch.setattr(tr, "_torchvision_factory_model", lambda config, path: factory(config, path, faithful=False))
    with pytest.raises(RuntimeError, match="nothing was exported"):
        export("faulty")
    assert not (tmp_path / "registry" / "penmarks" / "faulty").exists()


def test_the_reloaded_weights_must_reproduce_what_the_live_network_scored_in_training(trained, tmp_path):
    _, _, val_pages, run, _ = trained
    model, _, record = tr.load_run(run["run_dir"])
    predictions = tr.predict_pages(model, val_pages, device="cpu")
    check = tr.history_check(run["run_dir"], val_pages, predictions)
    assert check["ran"] and check["passed"] and check["epoch"] == record["best_epoch"]
    assert set(check["compared"]) >= {"val_selection_score", "val_ap50.exclusion", "val_detections.exclusion"}
    emptied = {image_id: ev.EMPTY for image_id in predictions}
    recorded = next(h for h in tr.read_history(run["run_dir"]) if h["epoch"] == record["best_epoch"])
    if recorded["val_detections"]["exclusion"]:
        assert not tr.history_check(run["run_dir"], val_pages, emptied)["passed"]
    skipped = tr.history_check(run["run_dir"], val_pages[:1], predictions)
    assert not skipped["ran"] and skipped["passed"] is None and "2 validation pages" in skipped["reason"]


def test_reload_outputs_go_to_their_own_folder_unless_overwriting_is_asked_for(tmp_path):
    run_eval, own = tr.evaluation_dirs(tmp_path, "run-1")
    assert run_eval == own == tmp_path / "evaluation" / "run-1"
    run_eval, separate = tr.evaluation_dirs(tmp_path, "run-1", reload=True, stamp="20261013T010203Z")
    assert run_eval == tmp_path / "evaluation" / "run-1" and separate == run_eval / "reload-20261013T010203Z"
    assert tr.evaluation_dirs(tmp_path, "run-1", reload=True)[1].name.startswith("reload-2")
    assert tr.evaluation_dirs(tmp_path, "run-1", reload=True, overwrite_run_outputs=True)[1] == run_eval


def test_the_final_evaluation_is_refused_for_a_quick_run_and_when_it_was_already_done(tmp_path):
    run_eval = tmp_path / "evaluation" / "run-1"
    run_eval.mkdir(parents=True)
    assert tr.final_evaluation_path(run_eval, quick_run=False) == run_eval / "final_test_report.json"
    with pytest.raises(RuntimeError, match="QUICK_RUN"):
        tr.final_evaluation_path(run_eval, quick_run=True)
    (run_eval / "final_test_report.json").write_text("{}")
    with pytest.raises(FileExistsError, match="already"):
        tr.final_evaluation_path(run_eval, quick_run=False)
