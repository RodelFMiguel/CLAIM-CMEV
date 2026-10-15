"""Training loop, inference, reload and registry export of the M5 line-item model.

A tiny randomly initialised LayoutLMv3 and hand-built pages stand in for the real ones: no
network, no dataset, no real checkpoint. These tests prove mechanics, never accuracy.
"""
from __future__ import annotations

import json
from types import MappingProxyType

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, serving_preprocessing  # noqa: E402
from pipelines.documents.lineitems_data import (  # noqa: E402
    IGNORE_INDEX,
    LabelPolicy,
    PageWindowDataset,
    align_pages,
    bio_labels,
    region_label_names,
)
from pipelines.documents.lineitems_eval import validation_report  # noqa: E402
import pipelines.documents.lineitems_training as training  # noqa: E402
from pipelines.documents.lineitems_training import (  # noqa: E402
    MANIFEST_FIELDS,
    REQUIRED_ENTRY_FILES,
    ROWS_FOR_M6_SCHEMA,
    TEST_REPORT_NAME,
    TrainConfig,
    build_model,
    describe_training_data,
    entry_problems,
    evaluate_pages,
    evaluation_folder_name,
    export_for_serving,
    load_run,
    lr_factor,
    masked_cross_entropy,
    new_run_id,
    open_final_evaluation,
    parameter_blocks,
    recorded_evaluation_dir,
    registry_entry_with_weights,
    reload_mismatches,
    rows_for_m6,
    run_inference,
    train,
    write_json_atomic,
)
from pipelines.documents.m4_cache import M4Cache  # noqa: E402
from test_m5_layoutlmv3 import long_cells, page_image, tiny_checkpoint  # noqa: E402
from test_m5_lineitems_data import HANDWRITING, annotation, cached  # noqa: E402

CELLS = [("Claim Number", (60, 100, 200, 120), 0.98), ("MC2026000001", (298, 98, 484, 123), 0.99),
         ("Rear door panel LH", (198, 198, 464, 224), 0.95), ("715.00", (797, 197, 873, 222), 0.99),
         ("Nett585.00", (898, 198, 998, 242), 0.82), ("Front bumper", (198, 258, 384, 284), 0.97),
         ("415.00", (797, 257, 873, 282), 0.99)]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Six hand-built pages (three claims) behind a minimal M4 cache, and a tiny base checkpoint."""
    root = tmp_path_factory.mktemp("lineitems-training")
    image = page_image(root)
    layout = [("C-1", "train"), ("C-2", "train"), ("C-3", "validation")]
    pages, annotations = [], {}
    for claim, split in layout:
        for number, cells in ((1, CELLS[:2]), (2, CELLS)):
            image_id = f"{claim}_P{number}"
            pages.append(cached(cells, image_id=image_id, split=split, claim=claim, rectified=image))
            annotations[image_id] = annotation(image_id, split, handwriting=HANDWRITING)
    pages.append(cached([], image_id="C-3_P3", split="validation", claim="C-3", rectified=image))  # M4 failed here
    annotations["C-3_P3"] = annotation("C-3_P3", "validation")
    labels = bio_labels(region_label_names(annotations))
    manifest = {"schema": "cmev-m4-cache/1",
                "dataset": {"name": "tiny-fixture", "manifest_sha256": "a" * 64, "annotations_sha256": "b" * 64,
                            "provenance_sha256": None, "page_count": len(pages)},
                "m4": {"versions": {"ocr_weights": "fixture-ocr"}, "m4_source_sha256": "c" * 64,
                       "git_commit": None, "git_dirty": None, "container_image": None}}
    (root / "cache").mkdir()
    (root / "cache" / "manifest.json").write_text(json.dumps(manifest))
    cache = M4Cache(root=root / "cache", manifest=MappingProxyType(manifest), pages=tuple(pages))
    base = tiny_checkpoint(root / "base", labels=labels)
    return {"root": root, "image": image, "cache": cache, "annotations": annotations, "labels": labels,
            "label2id": {label: index for index, label in enumerate(labels)}, "base": base,
            "policy": LabelPolicy()}


def config_for(world, **overrides) -> TrainConfig:
    settings = dict(base_checkpoint=str(world["base"]), revision=None, epochs=2, batch_size=2, accumulation_steps=2,
                    patience=5, device="cpu", learning_rate=1e-3)
    settings.update(overrides)
    return TrainConfig(**settings)


def datasets(world, processor):
    alignments = align_pages(world["cache"].pages, world["annotations"], world["policy"])
    make = lambda split: PageWindowDataset(list(world["cache"].split(split)), processor,  # noqa: E731
                                           alignments=alignments, label2id=world["label2id"])
    return alignments, make("train"), make("validation")


def scorer(world, alignments):
    held_out = {image_id: a for image_id, a in alignments.items() if a.split == "validation"}
    return lambda predictions: validation_report(held_out, predictions, world["annotations"])


def test_recipe_defaults_follow_the_specification():
    config = TrainConfig()
    assert (config.learning_rate, config.weight_decay, config.warmup_fraction) == (5e-5, 0.01, 0.10)
    assert config.effective_batch_size == 8 and config.epochs == 20 and config.seed == 20260922
    assert new_run_id(7).startswith("lineitems-layoutlmv3-") and new_run_id(7).endswith("-s7")


def test_learning_rate_warms_up_linearly_then_decays_to_zero():
    factors = [lr_factor(update, 100, 10) for update in range(100)]
    assert factors[0] == pytest.approx(0.1) and factors[9] == pytest.approx(1.0)
    assert factors[10] == pytest.approx(1.0) and factors[55] == pytest.approx(0.5)
    assert all(a >= b for a, b in zip(factors[10:], factors[11:])) and lr_factor(100, 100, 10) == 0.0


def test_masked_cross_entropy_ignores_unlabelled_positions():
    logits = torch.tensor([[[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [9.0, 9.0, 9.0]]])
    labels = torch.tensor([[0, 1, IGNORE_INDEX]])
    expected = torch.nn.functional.cross_entropy(logits[0, :2], labels[0, :2])
    assert masked_cross_entropy(logits, labels) == pytest.approx(float(expected))
    changed = logits.clone()
    changed[0, 2] = torch.tensor([-50.0, 0.0, 50.0])  # the ignored position cannot move the loss
    assert masked_cross_entropy(changed, labels) == pytest.approx(float(expected))


def test_in_memory_inference_equals_the_serving_adapter(world):
    """Same checkpoint, same pages: the notebook's batched inference and ``LocalLayoutLMv3.predict`` agree."""
    served = LocalLayoutLMv3(tiny_checkpoint(world["root"] / "served", labels=world["labels"],
                                             preprocessing=serving_preprocessing("line")))
    long_page = cached(long_cells(150), image_id="LONG", rectified=world["image"])
    blank = cached(CELLS[:3] + [("  ", (60, 300, 90, 320), 0.6)], image_id="BLANK", rectified=world["image"])
    failed = cached([], image_id="FAILED", rectified=world["image"])
    pages = [world["cache"].by_id("C-1_P2"), long_page, blank, failed]
    predictions, loss = run_inference(served.model, PageWindowDataset(pages, served.processor), "cpu", batch_size=2)
    assert loss is None and predictions["FAILED"] == ()
    for page in pages[:-1]:
        expected = served.predict(page.page, world["image"])
        got = predictions[page.image_id]
        assert [(p.box_id, p.label) for p in got] == [(p.box_id, p.label) for p in expected]
        assert all(abs(a.confidence - b.confidence) < 1e-5 for a, b in zip(got, expected))
    assert (predictions["BLANK"][-1].label, predictions["BLANK"][-1].confidence) == ("O", 0.0)


def test_train_keeps_the_best_epoch_and_records_the_run(world):
    model, processor, info = build_model(world["labels"], config_for(world))
    assert info["missing_keys"] == [] or all(key.startswith("classifier") for key in info["missing_keys"])
    blocks = parameter_blocks(model)
    assert sum(block["parameters"] for block in blocks) == sum(p.numel() for p in model.parameters())
    assert {block["block"]: block["starts_from"] for block in blocks}["classification head"] == "new, random"
    alignments, train_set, validation_set = datasets(world, processor)
    assert validation_set.skipped == [("C-3_P3", "unreadable")]
    run_dir, evaluation_dir = world["root"] / "runs" / "run-a", world["root"] / "evaluation" / "run-a"
    context = describe_training_data(world["cache"], alignments, world["policy"], world["labels"])
    lines: list[str] = []
    record = train(model, processor, train_set, validation_set, config_for(world), labels=world["labels"],
                   score=scorer(world, alignments), run_dir=run_dir, evaluation_dir=evaluation_dir, context=context,
                   log=lines.append)
    history = json.loads((run_dir / "history.json").read_text())
    assert record["status"] == "completed" and record["completed_epochs"] == len(history) == 2 == len(lines)
    assert record["schedule"] == {"batches_per_epoch": 2, "updates_per_epoch": 1, "total_updates": 2,
                                  "warmup_updates": 1, "effective_batch_size": 4}
    assert all(0 <= epoch["validation_entity_f1"] <= 1 and epoch["train_loss"] > 0 for epoch in history)
    assert record["best_epoch"] in (1, 2) and (run_dir / "best" / "model.safetensors").is_file()
    assert not (run_dir / "last.pt").exists()                       # removed once the run completed
    assert (run_dir / "best" / "preprocessor_config.json").is_file()
    assert not (run_dir / "best" / "processor_config.json").exists()  # the layout transformers 4 also loads
    assert record["data"]["validation_pages_without_text"] == ["C-3_P3"]
    report = json.loads((evaluation_dir / "val_metrics.json").read_text())
    assert report["split"] == "validation" and report["pages"] == 3 and report["pages_unreadable"] == 1
    assert json.loads((evaluation_dir / "manifest.json").read_text())["status"] == "completed"
    with pytest.raises(FileExistsError, match="never overwritten"):
        train(model, processor, train_set, validation_set, config_for(world), labels=world["labels"],
              score=scorer(world, alignments), run_dir=run_dir)

    # Reload without retraining: the saved weights give the labels the report was scored from.
    reloaded, reloaded_processor, labels, saved = load_run(run_dir)
    assert labels == world["labels"] and saved["run_id"] == "run-a"
    again, _ = run_inference(reloaded, PageWindowDataset(list(world["cache"].split("validation")), reloaded_processor),
                             "cpu")
    assert validation_report({k: v for k, v in alignments.items() if v.split == "validation"}, again,
                             world["annotations"])["entities"] == report["entities"]


def test_training_changes_the_weights_and_the_data_description_keeps_test_closed(world):
    model, processor, _ = build_model(world["labels"], config_for(world))
    before = model.classifier.weight.detach().clone() if hasattr(model.classifier, "weight") else \
        model.classifier.out_proj.weight.detach().clone()
    alignments, train_set, validation_set = datasets(world, processor)
    train(model, processor, train_set, validation_set, config_for(world, epochs=1), labels=world["labels"],
          score=scorer(world, alignments), run_dir=world["root"] / "runs" / "run-b", log=lambda _line: None)
    after = model.classifier.weight if hasattr(model.classifier, "weight") else model.classifier.out_proj.weight
    assert not torch.equal(before, after.detach().cpu())

    only_train = {image_id: a for image_id, a in alignments.items() if a.split == "train"}
    context = describe_training_data(world["cache"], only_train, world["policy"], world["labels"])
    assert context["splits"]["train"]["boxes_by_outcome"] == {"field": 12, "masked": 2, "other": 4}
    assert context["splits"]["validation"]["label_statistics"] is None       # not aligned: no label counts
    assert context["splits"]["validation"]["pages_unreadable"] == 1
    assert len(context["m4_cache"]["manifest_sha256"]) == 64 and context["label_policy"]["status"] == "proposed"


def test_an_interrupted_run_resumes_from_its_last_epoch(world):
    model, processor, _ = build_model(world["labels"], config_for(world))
    alignments, train_set, validation_set = datasets(world, processor)
    run_dir = world["root"] / "runs" / "run-c"
    score, calls = scorer(world, alignments), []

    def interrupted(predictions):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return score(predictions)

    with pytest.raises(KeyboardInterrupt):
        train(model, processor, train_set, validation_set, config_for(world, epochs=3), labels=world["labels"],
              score=interrupted, run_dir=run_dir, log=lambda _line: None)
    assert json.loads((run_dir / "manifest.json").read_text())["status"] == "interrupted"
    assert (run_dir / "last.pt").is_file() and len(json.loads((run_dir / "history.json").read_text())) == 1

    fresh, processor, _ = build_model(world["labels"], config_for(world))
    record = train(fresh, processor, train_set, validation_set, config_for(world, epochs=3), labels=world["labels"],
                   score=score, run_dir=run_dir, resume=True, log=lambda _line: None)
    history = json.loads((run_dir / "history.json").read_text())
    assert record["status"] == "completed" and record["resumed_from_epoch"] == 1
    assert [epoch["epoch"] for epoch in history] == [1, 2, 3]


def test_early_stopping_ends_a_run_that_stops_improving(world):
    model, processor, _ = build_model(world["labels"], config_for(world))
    alignments, train_set, validation_set = datasets(world, processor)
    score = scorer(world, alignments)

    def flat(predictions):
        report = score(predictions)
        report["entities"]["f1"] = 0.5      # never better than the first epoch
        return report

    record = train(model, processor, train_set, validation_set, config_for(world, epochs=6, patience=2),
                   labels=world["labels"], score=flat, run_dir=world["root"] / "runs" / "run-d", log=lambda _l: None)
    assert record["completed_epochs"] == 3 and record["best_epoch"] == 1 and record["stopped_early"] is True


def test_the_final_evaluation_function_runs_on_any_pages_and_keeps_failed_pages_in_the_count(world):
    """The opt-in test evaluation calls this same function; here it is pointed at the validation pages."""
    model, processor, _ = build_model(world["labels"], config_for(world))
    result = evaluate_pages(model, processor, list(world["cache"].split("validation")), world["annotations"],
                            world["policy"], world["labels"], device="cpu")
    report = result["report"]
    assert report["pages"] == 3 and report["pages_unreadable"] == 1 and report["splits"] == ["validation"]
    assert result["predictions"]["C-3_P3"] == () and result["loss"] > 0
    assert report["boxes"]["boxes"] == 9 and report["boxes"]["masked_boxes"] == 1
    assert report["rows"]["rows"] == 6 and report["rows"]["rows_on_unreadable_pages"] == 2


def test_export_writes_a_complete_candidate_that_the_serving_adapter_loads(world, tmp_path):
    model, processor, _ = build_model(world["labels"], config_for(world))
    alignments, train_set, validation_set = datasets(world, processor)
    run_dir, evaluation_dir = world["root"] / "runs" / "run-e", world["root"] / "evaluation" / "run-e"
    train(model, processor, train_set, validation_set, config_for(world, epochs=1), labels=world["labels"],
          score=scorer(world, alignments), run_dir=run_dir, evaluation_dir=evaluation_dir,
          context=describe_training_data(world["cache"], alignments, world["policy"], world["labels"]),
          log=lambda _line: None)
    check = [world["cache"].by_id("C-3_P2"), world["cache"].by_id("C-3_P1")]
    arguments = dict(check_pages=check, created_by="unit test", known_limits="tiny random model",
                     targets={"complete_entry_f1": {"target": 0.75, "status": "not_evaluated"}},
                     log=lambda _line: None)
    with pytest.raises(ValueError, match="lineitems/<name>"):
        export_for_serving(run_dir, "parts/run-e", tmp_path, **arguments)
    with pytest.raises(ValueError, match="check_pages"):
        export_for_serving(run_dir, "lineitems/run-e", tmp_path, **{**arguments, "check_pages": []})
    entry = export_for_serving(run_dir, "lineitems/run-e-serving", tmp_path, **arguments)
    assert entry == tmp_path / "lineitems" / "run-e-serving" and entry_problems(entry) == []
    assert all((entry / name).is_file() for name in REQUIRED_ENTRY_FILES)
    assert [p.name for p in entry.parent.iterdir()] == ["run-e-serving"]      # no staging folder left behind
    manifest = json.loads((entry / "manifest.json").read_text())
    assert all(name in manifest for name in MANIFEST_FIELDS)
    assert (manifest["status"], manifest["provenance"], manifest["task"]) == ("candidate", "synthetic",
                                                                              "token_classification")
    assert manifest["taxonomy_version"] is None and manifest["taxonomy_version_reason"]
    assert manifest["m4_cache"]["versions"] == {"ocr_weights": "fixture-ocr"}
    assert manifest["metrics"]["partition"] == "validation" and manifest["metrics"]["pages_unreadable"] == 1
    preprocessing = json.loads((entry / "preprocessing.json").read_text())
    assert preprocessing["text_unit_granularity"] == "line" and preprocessing["label_rule"] == "first_subword"
    assert preprocessing["label_alignment_policy"]["version"] == world["policy"].version
    assert json.loads((entry / "split_manifest.json").read_text())["split_hashes"].keys() == {"train", "validation"}
    assert "cc-by-nc-sa-4.0" in (entry / "NOTICE.md").read_text()

    served = LocalLayoutLMv3(entry)
    assert served.text_unit == "line" and served.model_version == "sha256:" + manifest["weights_sha256"]
    reloaded, reloaded_processor, _, _ = load_run(run_dir)
    expected, _ = run_inference(reloaded, PageWindowDataset(check, reloaded_processor), "cpu")
    for page in check:
        assert [p.label for p in served.predict(page.page, page.rectified_path)] == \
            [p.label for p in expected[page.image_id]]
    with pytest.raises(FileExistsError, match="not replaced"):
        export_for_serving(run_dir, "lineitems/run-e-serving", tmp_path, **arguments)

    # A manifest that silently drops a field, or weights that changed, is reported.
    broken = dict(manifest, hardware=None)
    (entry / "manifest.json").write_text(json.dumps(broken))
    assert entry_problems(entry) == ["manifest field hardware is null without hardware_reason"]
    with (entry / "model.safetensors").open("ab") as stream:
        stream.write(b"x")
    assert "weights_sha256 does not match model.safetensors" in entry_problems(entry)


# --------------------------------------------------------------------------- guards of the notebook's switches

def test_the_final_evaluation_refuses_a_quick_run_and_a_second_opening(tmp_path):
    """The held-out split is opened once per run, and never by a plumbing check. No split is read here."""
    folder = tmp_path / "evaluation" / "run-x"
    with pytest.raises(RuntimeError, match="QUICK_RUN"):
        open_final_evaluation(folder, run_id="run-x", quick_run=True)
    assert not folder.exists()                                           # the refusal left no trace of an opening
    path = open_final_evaluation(folder, run_id="run-x", quick_run=False)
    assert path == folder / TEST_REPORT_NAME == folder / "test_metrics.json"
    opened = json.loads(path.read_text())
    assert opened["status"] == "opened" and opened["run_id"] == "run-x" and opened["split"] == "test"
    with pytest.raises(RuntimeError, match=r"already opened.*run-x/test_metrics\.json"):
        open_final_evaluation(folder, run_id="run-x", quick_run=False)
    write_json_atomic(path, {"split": "test", "status": "completed"})   # the finished report replaces the marker
    with pytest.raises(RuntimeError, match="already opened"):
        open_final_evaluation(folder, run_id="run-x", quick_run=False)
    assert json.loads(path.read_text())["status"] == "completed"         # and a refusal never touches it


def test_a_reloaded_run_must_match_the_present_label_policy_and_m4_cache(world):
    alignments = align_pages(world["cache"].pages, world["annotations"], world["policy"])
    record = {"context": describe_training_data(world["cache"], alignments, world["policy"], world["labels"])}
    assert reload_mismatches(record, world["policy"], world["cache"]) == []
    stricter = LabelPolicy(field_overlap_min=0.7)
    (problem,) = reload_mismatches(record, stricter, world["cache"])
    assert "label policy" in problem and "field_overlap_min" in problem and "0.5" in problem and "0.7" in problem
    other = dict(record["context"], m4_cache=dict(record["context"]["m4_cache"], manifest_sha256="f" * 64))
    (problem,) = reload_mismatches({"context": other}, world["policy"], world["cache"])
    assert "M4 cache" in problem and "f" * 64 in problem and str(world["cache"].root / "manifest.json") in problem
    assert len(reload_mismatches({"context": {}}, world["policy"], world["cache"])) == 2   # nothing recorded: no match


def test_a_quick_execution_never_shares_the_report_folder_of_a_full_run():
    full, quick = {"context": {"quick_run": False}}, {"context": {"quick_run": True}}
    assert evaluation_folder_name("run-1", quick_run=False, reloaded=None) == "run-1"
    assert evaluation_folder_name("run-1-quick", quick_run=True, reloaded=None) == "run-1-quick"   # its own run id
    assert evaluation_folder_name("run-1", quick_run=False, reloaded=full) == "run-1"
    assert evaluation_folder_name("run-1", quick_run=True, reloaded=full) == "run-1-quick-check"   # full run stays intact
    assert evaluation_folder_name("run-1", quick_run=True, reloaded={"context": {}}) == "run-1-quick-check"
    assert evaluation_folder_name("run-1-quick", quick_run=True, reloaded=quick) == "run-1-quick"


def test_the_evaluation_folder_is_recorded_relative_to_the_checkout_so_a_moved_run_still_exports(world, tmp_path,
                                                                                                 monkeypatch):
    import shutil

    first = tmp_path / "checkout-a"
    monkeypatch.setattr(training, "REPOSITORY_ROOT", first)
    model, processor, _ = build_model(world["labels"], config_for(world))
    alignments, train_set, validation_set = datasets(world, processor)
    run_dir, evaluation_dir = first / "artifacts/models/lineitems/run-m", first / "artifacts/evaluation/run-m"
    record = train(model, processor, train_set, validation_set, config_for(world, epochs=1), labels=world["labels"],
                   score=scorer(world, alignments), run_dir=run_dir, evaluation_dir=evaluation_dir,
                   context=describe_training_data(world["cache"], alignments, world["policy"], world["labels"]),
                   log=lambda _line: None)
    assert record["evaluation_dir"] == "artifacts/evaluation/run-m"       # no machine-specific prefix
    assert recorded_evaluation_dir(record) == evaluation_dir

    second = tmp_path / "elsewhere" / "checkout-b"                        # the whole checkout moves
    second.parent.mkdir()
    shutil.move(str(first), str(second))
    monkeypatch.setattr(training, "REPOSITORY_ROOT", second)
    moved_run = second / "artifacts/models/lineitems/run-m"
    assert recorded_evaluation_dir(load_run(moved_run)[3]) == second / "artifacts/evaluation/run-m"
    entry = export_for_serving(moved_run, "lineitems/run-m-serving", second / "registry",
                               check_pages=[world["cache"].by_id("C-3_P2")], created_by="unit test",
                               known_limits="tiny random model", log=lambda _line: None)
    assert entry_problems(entry) == []

    # A run recorded before this change, or one outside the checkout, keeps its absolute path.
    assert recorded_evaluation_dir({"evaluation_dir": "/somewhere/else/evaluation/run-old"}).as_posix() == \
        "/somewhere/else/evaluation/run-old"
    assert recorded_evaluation_dir({"evaluation_dir": None}) is None and recorded_evaluation_dir({}) is None
    outside = train(build_model(world["labels"], config_for(world))[0], processor, train_set, validation_set,
                    config_for(world, epochs=1), labels=world["labels"], score=scorer(world, alignments),
                    run_dir=tmp_path / "outside" / "run-o", evaluation_dir=tmp_path / "outside" / "evaluation-o",
                    log=lambda _line: None)
    assert outside["evaluation_dir"] == str(tmp_path / "outside" / "evaluation-o")


# --------------------------------------------------------------------------- the rows M6 receives

def test_rows_for_m6_lists_every_estimate_page_with_the_row_and_amount_boxes_of_its_line_items(world, tmp_path):
    from claim_cmev.contracts.common import make_job_key
    from claim_cmev.documents.line_items import load_estimate_vocabulary, load_layout_families
    from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction, adapt_predictions
    from claim_cmev.documents.line_items.parser import pen_mark_row_boxes
    from m5_support import PROVENANCE

    estimate, failed = world["cache"].by_id("C-3_P2"), world["cache"].by_id("C-3_P3")
    survey = cached(CELLS[:2], image_id="C-3_P1", split="validation", claim="C-3", page_type="survey_assessment",
                    rectified=world["image"])
    truth = ["O", "B-claim_number", "B-part_1_description", "B-part_1_amount", "O", "B-part_2_description", "O"]
    predictions = {estimate.page.page_id: [WordPrediction(box.box_id, label, 0.9)
                                           for box, label in zip(estimate.page.text_boxes, truth)],
                   survey.page.page_id: [WordPrediction(box.box_id, "O", 0.9) for box in survey.page.text_boxes],
                   failed.page.page_id: ()}
    pages = [survey, estimate, failed]
    result = adapt_predictions(
        [page.page for page in pages], predictions, config=load_layout_families(), vocabulary=load_estimate_vocabulary(),
        family_id="family-a-ruled-grid", job_key=make_job_key(estimate.claim_id, 1, "line_items_extract", {"m": "t"}),
        claim_id=estimate.claim_id, input_revision=1, currency="SGD", cost_basis="single_part_pre_tax_no_discount_v1",
        model_version="sha256:" + "a" * 64, provenance=PROVENANCE)
    record = rows_for_m6(pages, {"C-3": result}, split="validation", quick_run=False, model_version="sha256:" + "a" * 64,
                         registry_entry=None, m4_cache_manifest_sha256="b" * 64, family_id="family-a-ruled-grid",
                         min_confidence=0.5, produced_by="unit test")
    assert {key: value for key, value in record.items() if key != "pages"} == {
        "schema": ROWS_FOR_M6_SCHEMA, "split": "validation", "provenance": "synthetic", "quick_run": False,
        "m5_model_version": "sha256:" + "a" * 64, "m5_registry_entry": None, "m4_cache_manifest_sha256": "b" * 64,
        "family_id": "family-a-ruled-grid", "min_confidence": 0.5, "produced_by": "unit test"}
    assert ROWS_FOR_M6_SCHEMA == "cmev-m5-rows-for-m6/1"
    assert [page["image_id"] for page in record["pages"]] == ["C-3_P2", "C-3_P3"]    # estimate pages only, failed kept
    first, second = record["pages"]
    assert {key: value for key, value in first.items() if key != "row_boxes"} == {
        "image_id": "C-3_P2", "page_id": estimate.page.page_id, "claim_id": estimate.claim_id,
        "dataset_claim_id": "C-3", "page_status": "complete"}
    assert first["row_boxes"] == [row for row in pen_mark_row_boxes(result) if row["page_id"] == estimate.page.page_id]
    assert len(first["row_boxes"]) == 2 and set(first["row_boxes"][0]) == {"entry_id", "page_id", "row_box_norm",
                                                                         "amount_box_norm"}
    # Row 1: the union of its description and amount boxes, not the printed table band. Row 2 has no amount box.
    assert first["row_boxes"][0]["row_box_norm"] == pytest.approx([198 / 1000, 197 / 1400, 873 / 1000, 224 / 1400])
    assert first["row_boxes"][0]["amount_box_norm"] == pytest.approx([797 / 1000, 197 / 1400, 873 / 1000, 222 / 1400])
    assert first["row_boxes"][1]["amount_box_norm"] is None
    assert second == {"image_id": "C-3_P3", "page_id": failed.page.page_id, "claim_id": failed.claim_id,
                      "dataset_claim_id": "C-3", "page_status": "unreadable", "row_boxes": []}
    with pytest.raises(KeyError, match="C-9"):                           # a claim without a result is not an empty page
        rows_for_m6([cached(CELLS, image_id="C-9_P2", claim="C-9", rectified=world["image"])], {"C-3": result},
                    split="validation", quick_run=False, model_version="v", registry_entry=None,
                    m4_cache_manifest_sha256="b" * 64, family_id="f", min_confidence=0.5, produced_by="unit test")

    # The file is replaced in one step, so a reader never sees half of it, and no temporary file is left behind.
    target = tmp_path / "evaluation" / "rows_for_m6_validation.json"
    target.parent.mkdir()
    target.write_text("{\"schema\": \"older file\"}")
    write_json_atomic(target, record)
    assert json.loads(target.read_text()) == json.loads(json.dumps(record))
    assert [path.name for path in target.parent.iterdir()] == ["rows_for_m6_validation.json"]


def test_the_rows_name_a_registry_entry_only_when_that_entry_holds_the_same_weights(tmp_path):
    entry = tmp_path / "lineitems" / "run-1-serving"
    entry.mkdir(parents=True)
    (entry / "manifest.json").write_text(json.dumps({"weights_sha256": "a" * 64}))
    assert registry_entry_with_weights(tmp_path, "lineitems/run-1-serving", "sha256:" + "a" * 64) == "lineitems/run-1-serving"
    assert registry_entry_with_weights(tmp_path, "lineitems/run-1-serving", "sha256:" + "c" * 64) is None
    assert registry_entry_with_weights(tmp_path, "lineitems/run-2-serving", "sha256:" + "a" * 64) is None
