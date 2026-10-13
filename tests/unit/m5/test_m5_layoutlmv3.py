"""Contract safety for the optional adapter, without importing neural dependencies."""
import pytest

from claim_cmev.contracts.common import ContractError
from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction, adapt_predictions
from m5_support import (BASIS, CLAIM_ID, CONFIG, JOB_KEY, PROVENANCE, VOCABULARY,
                        make_page, row_cells)


def adapt(pages, predictions):
    return adapt_predictions(
        pages, predictions, config=CONFIG, vocabulary=VOCABULARY,
        family_id="family-a-ruled-grid", job_key=JOB_KEY, claim_id=CLAIM_ID,
        input_revision=1, currency="SGD", cost_basis=BASIS,
        model_version="test-weights", provenance=PROVENANCE)


def test_exact_money_missing_quantity_stable_ids_and_source_links():
    page = make_page(row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200))
    predictions = {page.page_id: [WordPrediction(page.text_boxes[0].box_id, "B-part_1_description", .9),
                                  WordPrediction(page.text_boxes[1].box_id, "B-part_1_amount", .9)]}
    result = adapt([page], predictions)
    item = result.line_items[0]
    assert item.printed_line_amount == "980.00"
    assert item.quantity is None and item.unit_price is None
    assert item.part_code == "front-bumper"
    assert item.source_box_ids == [box.box_id for box in page.text_boxes]
    assert result.completeness.state == "partial"
    assert result.versions["extraction_method"] == "layoutlmv3"
    assert result.line_item_ids == adapt([page], predictions).line_item_ids


@pytest.mark.parametrize("amount,score,expected", [("48O.OO", .9, None), ("980.00", .1, None),
                                                  ("0.00", .9, "0.00")])
def test_unreadable_and_low_confidence_amounts_are_not_zero(amount, score, expected):
    page = make_page(row_cells({"description": "FRT BUMPER", "amount": amount}, 200))
    result = adapt([page], {page.page_id: [
        WordPrediction(page.text_boxes[0].box_id, "B-part_1_description", .9),
        WordPrediction(page.text_boxes[1].box_id, "B-part_1_amount", score)]})
    assert result.line_items[0].printed_line_amount == expected
    assert result.rows[0].field("amount").original_text == amount
    if score < .5:
        assert result.rows[0].field("amount").reason == "model_or_ocr_confidence_low"
        assert any(u.field == "printed_line_amount" and u.reason == "model_or_ocr_confidence_low"
                   for u in result.line_items[0].field_uncertainty)


def test_repeated_page_slots_keep_separate_ids():
    pages = [make_page(row_cells({"description": "FRT BUMPER"}, 200), page_number=n)
             for n in (1, 2)]
    predictions = {page.page_id: [WordPrediction(page.text_boxes[0].box_id,
                                               "B-part_1_description", .9)] for page in pages}
    assert len(set(adapt(pages, predictions).line_item_ids)) == 2


def test_no_rows_and_unreadable_are_never_explicitly_empty():
    page = make_page(row_cells({"description": "OTHER TEXT"}, 200))
    result = adapt([page], {page.page_id: [WordPrediction(page.text_boxes[0].box_id, "O", .9)]})
    assert result.completeness.state == "partial"
    assert "no_rows_matched" in result.completeness.reasons
    assert result.unparsed_regions[0].box_ids == (page.text_boxes[0].box_id,)
    unreadable = make_page([])
    assert adapt([unreadable], {}).completeness.state == "unreadable"


def test_incomplete_predictions_fail_instead_of_silent_omission():
    page = make_page(row_cells({"description": "FRT BUMPER"}, 200))
    with pytest.raises(ContractError, match="prediction_alignment_mismatch"):
        adapt([page], {})


# ---------------------------------------------------------------------------------------------
# Neural serving path. These tests build a tiny, randomly initialised LayoutLMv3 and a tiny
# byte-level tokenizer in a temporary folder: no network, no real checkpoint, no real page.
import json
import sys
from pathlib import Path

TINY_LABELS = ["O", "B-claim_number", "I-claim_number", "B-part_1_amount", "I-part_1_amount",
               "B-part_1_description", "I-part_1_description", "B-part_2_amount", "I-part_2_amount"]
_CORPUS = ("Rear door panel LH Replace Repair 715.00 980.00 Nett Not accepted SGD 585.00 Claim Number "
           "MC2026000001 Qty Amount Remarks FRT BUMPER OTHER TEXT 0 1 2 3 4 5 6 7 8 9")


def tiny_checkpoint(directory: Path, *, labels=TINY_LABELS, preprocessing=None) -> Path:
    """Write a loadable LayoutLMv3 folder (weights, config, tokenizer, image processor) into ``directory``."""
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from tokenizers import ByteLevelBPETokenizer

    tokenizer_class = getattr(transformers, "LayoutLMv3TokenizerFast", None) or transformers.LayoutLMv3Tokenizer
    directory.mkdir(parents=True, exist_ok=True)
    vocabulary = directory / "_tokenizer"
    vocabulary.mkdir()
    bpe = ByteLevelBPETokenizer()
    bpe.train_from_iterator([_CORPUS] * 4, vocab_size=400, min_frequency=1,
                            special_tokens=["<s>", "<pad>", "</s>", "<unk>", "<mask>"])
    bpe.save_model(str(vocabulary))
    files = {"vocab": str(vocabulary / "vocab.json"), "merges": str(vocabulary / "merges.txt")}
    if int(transformers.__version__.split(".")[0]) < 5:  # 4.x names the same two files differently
        files = {"vocab_file": files["vocab"], "merges_file": files["merges"]}
    tokenizer = tokenizer_class(**files)
    processor = transformers.LayoutLMv3Processor(transformers.LayoutLMv3ImageProcessor(apply_ocr=False), tokenizer)
    config = transformers.LayoutLMv3Config(
        vocab_size=len(tokenizer), hidden_size=32, num_hidden_layers=1, num_attention_heads=2, intermediate_size=48,
        max_position_embeddings=514, coordinate_size=6, shape_size=4, num_labels=len(labels),
        id2label=dict(enumerate(labels)), label2id={label: index for index, label in enumerate(labels)},
        pad_token_id=tokenizer.pad_token_id, bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id)
    torch.manual_seed(7)
    transformers.LayoutLMv3ForTokenClassification(config).save_pretrained(directory)
    processor.save_pretrained(directory)
    processor.image_processor.save_pretrained(directory)  # the layout both transformers 4 and 5 load
    (directory / "processor_config.json").unlink(missing_ok=True)
    if preprocessing is not None:
        (directory / "preprocessing.json").write_text(json.dumps(preprocessing), encoding="utf-8")
    return directory


def page_image(directory: Path, name: str = "page.png") -> Path:
    from PIL import Image
    from m5_support import H, W

    path = directory / name
    Image.new("RGB", (W, H), "white").save(path)
    return path


def long_cells(count: int):
    """``count`` line boxes of several subwords each: enough tokens to need overflow windows."""
    return [("Rear door panel LH 715.00 Nett", (60, 20 + 9 * i, 600, 28 + 9 * i), 0.95) for i in range(count)]


@pytest.fixture(scope="module")
def checkpoints(tmp_path_factory):
    from claim_cmev.documents.line_items.layoutlmv3 import serving_preprocessing

    root = tmp_path_factory.mktemp("tiny-layoutlmv3")
    return {"word": tiny_checkpoint(root / "word"),                      # declares nothing: the earlier checkpoint
            "line": tiny_checkpoint(root / "line", preprocessing=serving_preprocessing("line")),
            "image": page_image(root)}


def test_layout_bbox_truncates_to_the_0_999_grid():
    from claim_cmev.documents.line_items.layoutlmv3 import layout_bbox

    assert layout_bbox((0.0, 0.0199, 0.9999, 1.0)) == (0, 19, 999, 999)
    assert layout_bbox((-0.2, 0.5, 1.7, 0.5004)) == (0, 500, 999, 500)


def test_text_unit_is_declared_by_the_checkpoint_and_defaults_to_word(tmp_path, checkpoints):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, declared_text_unit

    assert declared_text_unit(checkpoints["word"]) == "word"          # no preprocessing.json at all
    assert declared_text_unit(checkpoints["line"]) == "line"
    (tmp_path / "preprocessing.json").write_text(json.dumps({"input_size": 224}))
    assert declared_text_unit(tmp_path) == "word"                     # file present, key absent
    (tmp_path / "preprocessing.json").write_text(json.dumps({"text_unit_granularity": "paragraph"}))
    with pytest.raises(ValueError, match="text_unit_granularity"):
        declared_text_unit(tmp_path)
    assert LocalLayoutLMv3(checkpoints["word"]).text_unit == "word"
    assert LocalLayoutLMv3(checkpoints["line"]).text_unit == "line"


def test_a_checkpoint_trained_with_other_encoding_settings_is_refused(tmp_path):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, serving_preprocessing

    tiny_checkpoint(tmp_path / "model", preprocessing={**serving_preprocessing("line"), "stride": 128})
    with pytest.raises(ValueError, match="stride: trained with 128"):
        LocalLayoutLMv3(tmp_path / "model")


def test_a_declared_text_unit_without_the_other_encoding_keys_is_refused(tmp_path):
    """A registry entry that names its text unit must say how its pages were encoded; nothing is defaulted."""
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, serving_preprocessing

    tiny_checkpoint(tmp_path / "partial", preprocessing={"text_unit_granularity": "line"})
    with pytest.raises(ValueError, match="does not declare.*text_normalisation.*max_length.*stride"):
        LocalLayoutLMv3(tmp_path / "partial")
    complete = serving_preprocessing("line")
    for index, missing in enumerate(key for key in complete if key != "text_unit_granularity"):
        folder = tiny_checkpoint(tmp_path / f"without-{index}",
                                 preprocessing={key: value for key, value in complete.items() if key != missing})
        with pytest.raises(ValueError, match=f"does not declare {missing}"):
            LocalLayoutLMv3(folder)


def test_a_registry_entry_whose_weights_differ_from_its_manifest_is_refused(tmp_path):
    """Fail closed on a damaged entry; a folder without a manifest (the earlier checkpoint) loads as before."""
    import hashlib

    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, serving_preprocessing

    folder = tiny_checkpoint(tmp_path / "entry", preprocessing=serving_preprocessing("line"))
    actual = hashlib.sha256((folder / "model.safetensors").read_bytes()).hexdigest()
    assert LocalLayoutLMv3(folder).model_version == "sha256:" + actual          # no manifest.json: nothing to compare
    (folder / "manifest.json").write_text(json.dumps({"model_id": "lineitems", "status": "candidate"}))
    assert LocalLayoutLMv3(folder).model_version == "sha256:" + actual          # a manifest that records no hash
    (folder / "manifest.json").write_text(json.dumps({"weights_sha256": actual}))
    assert LocalLayoutLMv3(folder).model_version == "sha256:" + actual
    (folder / "manifest.json").write_text(json.dumps({"weights_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="manifest.json records weights 000000.*model.safetensors is " + actual[:12]):
        LocalLayoutLMv3(folder)
    (folder / "manifest.json").write_text(json.dumps({"weights_sha256": actual}))
    with (folder / "model.safetensors").open("ab") as stream:                   # the weights file changed afterwards
        stream.write(b"x")
    with pytest.raises(ValueError, match="manifest.json records weights"):
        LocalLayoutLMv3(folder)


def test_predict_refuses_boxes_that_are_not_the_declared_text_unit(checkpoints):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    cells = row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200)
    line_page, word_page = make_page(cells, granularity="line"), make_page(cells, granularity="word")
    with pytest.raises(ValueError, match="word boxes.*line"):
        LocalLayoutLMv3(checkpoints["word"]).predict(line_page, checkpoints["image"])
    with pytest.raises(ValueError, match="line boxes.*word"):
        LocalLayoutLMv3(checkpoints["line"]).predict(word_page, checkpoints["image"])


@pytest.mark.parametrize("unit", ["word", "line"])
def test_predict_returns_one_prediction_per_box_in_reading_order(checkpoints, unit):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    page = make_page(row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200), granularity=unit)
    model = LocalLayoutLMv3(checkpoints[unit])
    predictions = model.predict(page, checkpoints["image"])
    assert [p.box_id for p in predictions] == [box.box_id for box in page.text_boxes]
    assert all(p.label in TINY_LABELS and 0 <= p.confidence <= 1 for p in predictions)
    assert model.predict(page, checkpoints["image"]) == predictions  # inference is deterministic


def test_overflow_windows_cover_every_box_once_and_stack_the_page_image(checkpoints):
    torch = pytest.importorskip("torch")
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    page = make_page(long_cells(150), granularity="line")
    model = LocalLayoutLMv3(checkpoints["line"])
    words, encoding = model.encode(page, checkpoints["image"])
    windows = encoding["input_ids"].shape[0]
    assert windows >= 3 and len(words.texts) == 150
    assert isinstance(encoding["pixel_values"], torch.Tensor)
    assert tuple(encoding["pixel_values"].shape) == (windows, 3, 224, 224)
    predictions = model.predict(page, checkpoints["image"])
    assert [p.box_id for p in predictions] == [box.box_id for box in page.text_boxes]


def test_only_a_first_subword_carries_a_training_label_even_at_a_window_start(checkpoints):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, encode_page, page_words
    from PIL import Image

    page = make_page(long_cells(150), granularity="line")
    model = LocalLayoutLMv3(checkpoints["line"])
    words = page_words(page)
    with Image.open(checkpoints["image"]) as source:
        encoding = encode_page(model.processor, source.convert("RGB"), words, word_labels=[3] * 150)
    labels, starts = encoding["labels"], encoding["offset_mapping"][..., 0]
    assert encoding["input_ids"].shape[0] >= 3
    assert bool((labels[starts != 0] == -100).all())
    for window in range(labels.shape[0]):
        ids = encoding.word_ids(batch_index=window)
        first = next(index for index, word in enumerate(ids) if word is not None)
        if window and int(starts[window, first]) != 0:  # this window starts inside a box's text
            assert int(labels[window, first]) == -100
        labelled = [ids[index] for index in range(len(ids)) if int(labels[window, index]) != -100]
        assert len(labelled) == len(set(labelled))  # at most one labelled token per box and window
    assert {word for window in range(labels.shape[0]) for index, word in enumerate(encoding.word_ids(window))
            if int(labels[window, index]) != -100} == set(range(150))


def test_encoding_equals_the_plain_processor_call_for_ordinary_text(checkpoints):
    """The shared encoder is the call the adapter always made; a word checkpoint sees the same tensors."""
    torch = pytest.importorskip("torch")
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3
    from PIL import Image

    page = make_page(row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200), granularity="word")
    model = LocalLayoutLMv3(checkpoints["word"])
    words, encoding = model.encode(page, checkpoints["image"])
    with Image.open(checkpoints["image"]) as source:
        plain = model.processor(
            images=source.convert("RGB"), text=[box.text for box in page.text_boxes],
            boxes=[[min(999, max(0, int(value * 1000))) for value in box.box_norm] for box in page.text_boxes],
            padding="max_length", truncation=True, max_length=512, stride=64,
            return_overflowing_tokens=True, return_offsets_mapping=True, return_tensors="pt")
    assert words.readable == (0, 1)
    for key in ("input_ids", "attention_mask", "bbox"):
        assert torch.equal(encoding[key], plain[key]), key
    assert torch.equal(encoding["pixel_values"], torch.stack([torch.as_tensor(v) for v in plain["pixel_values"]]))


def predict_as_before_text_units_were_declared(model, page, image_path):
    """``LocalLayoutLMv3.predict`` of commit ec8bcba, kept here as the reference: raw box text, every box a word."""
    from claim_cmev.documents.line_items.layoutlmv3 import WordPrediction
    from PIL import Image

    torch = model.torch
    boxes = sorted(page.text_boxes, key=lambda box: box.order_index)
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    encoding = model.processor(
        images=image, text=[box.text for box in boxes],
        boxes=[[min(999, max(0, int(value * 1000))) for value in box.box_norm] for box in boxes],
        padding="max_length", truncation=True, max_length=512, stride=64,
        return_overflowing_tokens=True, return_offsets_mapping=True, return_tensors="pt")
    if isinstance(encoding["pixel_values"], list):
        encoding["pixel_values"] = torch.stack([torch.as_tensor(value) for value in encoding["pixel_values"]])
    predictions = {}
    for window in range(len(encoding["input_ids"])):
        inputs = {key: encoding[key][window:window + 1] for key in ("input_ids", "attention_mask", "bbox", "pixel_values")}
        with torch.inference_mode():
            probabilities = model.model(**inputs).logits[0].softmax(-1)
        for token, word in enumerate(encoding.word_ids(batch_index=window)):
            if word is None or word in predictions or int(encoding["offset_mapping"][window, token, 0]) != 0:
                continue
            score, label_id = probabilities[token].max(-1)
            predictions[word] = WordPrediction(boxes[word].box_id, model.model.config.id2label[int(label_id)], float(score))
    if len(predictions) != len(boxes):
        raise ValueError("Tokenization did not cover every OCR word; no partial success written")
    return encoding, tuple(predictions[index] for index in range(len(boxes)))


SPACED_CELLS = [(" Rear  door panel LH", (60, 200, 380, 220), 0.95), ("715.00  Nett ", (800, 200, 900, 220), 0.95),
                ("  Not   accepted", (60, 300, 300, 320), 0.9)]


def test_a_checkpoint_that_declares_nothing_gets_the_text_handling_it_always_had(checkpoints):
    """Whitespace is collapsed only for a checkpoint that declares it; the earlier checkpoint sees raw box text."""
    torch = pytest.importorskip("torch")
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    page = make_page(SPACED_CELLS, granularity="word")
    model = LocalLayoutLMv3(checkpoints["word"])
    before, expected = predict_as_before_text_units_were_declared(model, page, checkpoints["image"])
    words, encoding = model.encode(page, checkpoints["image"])
    assert words.texts == tuple(box.text for box in page.text_boxes) and words.readable == (0, 1, 2)   # untouched text
    for key in ("input_ids", "attention_mask", "bbox", "pixel_values"):
        assert torch.equal(encoding[key], before[key]), key
    assert model.predict(page, checkpoints["image"]) == expected                    # labels and scores, exactly

    # The doubled spaces are real tokens for this tokenizer, so collapsing them would have changed the tensors ...
    declared = LocalLayoutLMv3(checkpoints["line"])
    collapsed, collapsed_encoding = declared.encode(make_page(SPACED_CELLS, granularity="line"), checkpoints["image"])
    assert collapsed.texts == ("Rear door panel LH", "715.00 Nett", "Not accepted")
    assert int(collapsed_encoding["attention_mask"].sum()) < int(before["attention_mask"].sum())
    # ... and a checkpoint that declares the normalisation gets it.
    assert declared.text_normalisation == "collapse_whitespace" and model.text_normalisation is None


def test_a_blank_box_is_handled_for_an_undeclared_checkpoint_exactly_as_before(checkpoints):
    """The placeholder for a box without visible text belongs to declared checkpoints only."""
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    cells = row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200) + [("   ", (60, 300, 90, 320), 0.6)]
    page = make_page(cells, granularity="word")
    model = LocalLayoutLMv3(checkpoints["word"])
    try:
        _, expected = predict_as_before_text_units_were_declared(model, page, checkpoints["image"])
    except ValueError as error:                      # some tokenizer versions give a blank word no token
        with pytest.raises(ValueError, match="did not cover every OCR word"):
            model.predict(page, checkpoints["image"])
        assert "did not cover every OCR word" in str(error)
    else:
        assert model.predict(page, checkpoints["image"]) == expected


def test_a_box_without_visible_text_is_reported_as_unassigned_evidence_not_dropped(checkpoints):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3, page_words

    cells = row_cells({"description": "FRT BUMPER", "amount": "980.00"}, 200) + [("   ", (60, 300, 90, 320), 0.6),
                                                                              (" Not  accepted ", (60, 340, 300, 360), 0.9)]
    page = make_page(cells, granularity="line")
    words = page_words(page)
    assert words.readable == (0, 1, 3) and words.texts[-1] == "Not accepted"
    predictions = LocalLayoutLMv3(checkpoints["line"]).predict(page, checkpoints["image"])
    assert [p.box_id for p in predictions] == [box.box_id for box in page.text_boxes]
    assert (predictions[2].label, predictions[2].confidence) == ("O", 0.0)
    result = adapt([page], {page.page_id: list(predictions)})
    assert page.text_boxes[2].box_id in {box_id for region in result.unparsed_regions for box_id in region.box_ids}


def test_unreadable_page_gives_no_predictions(checkpoints):
    from claim_cmev.documents.line_items.layoutlmv3 import LocalLayoutLMv3

    assert LocalLayoutLMv3(checkpoints["line"]).predict(make_page([]), checkpoints["image"]) == ()


def test_extraction_command_accepts_the_apple_gpu_device(tmp_path, monkeypatch, capsys):
    """``--device mps`` reaches the model loader; the fake loader keeps this test free of weights."""
    import importlib.util

    import claim_cmev.documents.line_items.layoutlmv3 as adapter

    seen = {}

    class FakeModel:
        model_version = "sha256:test"

        def __init__(self, model_dir, device):
            seen["device"] = device

    monkeypatch.setattr(adapter, "LocalLayoutLMv3", FakeModel)
    page = make_page([])  # an unreadable page needs no inference
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({
        "claim_id": CLAIM_ID, "input_revision": 1,
        "provenance": {"source_kind": "fixture", "runtime_profile": "full", "producer_service": "test"},
        "pages": [{"image_path": "unused.png", "page": page.model_dump(mode="json")}]}))
    script = Path(__file__).resolve().parents[3] / "scripts" / "extract_line_items_layoutlmv3.py"
    spec = importlib.util.spec_from_file_location("extract_line_items_layoutlmv3", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(script), str(manifest), "--output", str(tmp_path / "out.json"),
                                      "--family-id", "family-a-ruled-grid", "--currency", "SGD",
                                      "--cost-basis", BASIS, "--device", "mps"])
    assert module.main() == 0
    assert seen == {"device": "mps"}
    assert json.loads((tmp_path / "out.json").read_text())["completeness"]["state"] == "unreadable"
