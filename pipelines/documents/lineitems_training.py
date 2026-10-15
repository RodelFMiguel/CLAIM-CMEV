"""Fine-tune LayoutLMv3 to label M4 text boxes, keep the run, and hand it to the serving adapter.

``train`` runs the loop the M5 notebook explains: AdamW, linear warmup then linear decay,
masked cross-entropy on the first subword of every labelled box, one validation pass per
epoch, the best epoch kept by validation entity F1, early stopping. A run is a folder under
``artifacts/models/lineitems/<run_id>/``; it is a candidate and nothing here serves it.

``export_for_serving`` writes a run as a registry entry (training specification 10.2): the
weights in the layout the serving adapter loads on transformers 4 and 5, plus the manifest,
label schema, preprocessing record, split manifest, evaluation report, training log, config
and notice. The entry appears only after ``LocalLayoutLMv3`` loaded the staged copy and
reproduced the run's labels on real pages. An existing entry is never replaced.

``rows_for_m6`` writes down what M6 receives from this model: the row and amount boxes of
the line items the serving code produced, which M6's linker compares pen marks with.

No test-split page is read by anything in this module unless a caller passes one in, and
``open_final_evaluation`` lets a caller do that once per run and never in a quick run.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import random
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

from claim_cmev.contracts.common import SCHEMA_VERSION
from claim_cmev.documents.line_items.layoutlmv3 import (
    ALIGNMENT_VERSION,
    UNREAD_LABEL,
    LocalLayoutLMv3,
    WordPrediction,
    serving_preprocessing,
)
from claim_cmev.documents.line_items.parser import pen_mark_row_boxes
from claim_cmev.vision.registry import sha256_file
from pipelines.documents.lineitems_data import (
    IGNORE_INDEX,
    LabelPolicy,
    PageAlignment,
    PageWindowDataset,
    collate_windows,
)
from pipelines.documents.m4_cache import CachedPage, M4Cache
from pipelines.vision.training import select_device

MODEL_ID = "lineitems"
BASE_CHECKPOINT = "microsoft/layoutlmv3-base"
BASE_REVISION = "cfbbbff0762e6aab37086fdd4739ad14fe7d5db4"
BASE_LICENCE = "cc-by-nc-sa-4.0"
TEXT_UNIT = "line"  # M4 delivers line boxes; this model labels one line box at a time
PREPROCESSING_VERSION = "m5-layoutlmv3-preprocessing/0.1.0"
LABEL_SCHEMA_VERSION = "m5-box-slot-labels/0.1.0"
MODEL_INPUTS = ("input_ids", "attention_mask", "bbox", "pixel_values")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # recorded paths inside the checkout are kept relative to it
TEST_REPORT_NAME = "test_metrics.json"  # its presence means the held-out split was opened for the run
ROWS_FOR_M6_SCHEMA = "cmev-m5-rows-for-m6/1"
REQUIRED_ENTRY_FILES = ("model.safetensors", "config.json", "preprocessor_config.json", "tokenizer.json",
                        "manifest.json", "label_schema.json", "preprocessing.json", "split_manifest.json",
                        "evaluation_report.json", "training_log.jsonl", "config.yaml", "NOTICE.md")
MANIFEST_FIELDS = (
    "model_id", "version", "status", "created_at", "created_by", "task", "architecture", "base_checkpoint",
    "base_checkpoint_licence", "weights_sha256", "label_schema_version", "taxonomy_version",
    "preprocessing_version", "dataset_ids", "dataset_access_evidence", "split_version", "split_hashes",
    "conversion_version", "seed", "hardware", "dependency_versions", "code_revision", "training_duration_s",
    "metrics", "targets", "known_limits", "provenance", "contract_version")


@dataclass
class TrainConfig:
    """The recipe of one run. Every default is a proposed value (training specification 7.5)."""

    base_checkpoint: str = BASE_CHECKPOINT
    revision: str | None = BASE_REVISION
    learning_rate: float = 5e-5
    weight_decay: float = 0.01
    warmup_fraction: float = 0.10  # share of all optimiser updates spent warming up
    batch_size: int = 4  # windows per forward pass
    accumulation_steps: int = 2  # forward passes per optimiser update: 4 x 2 = the specified 8
    epochs: int = 20
    patience: int = 5  # epochs without a better validation entity F1 before stopping
    min_delta: float = 0.0
    max_grad_norm: float = 1.0
    eval_batch_size: int = 8
    seed: int = 20260922
    device: str = "auto"
    offline: bool = True  # load the base checkpoint from the local cache only

    @property
    def effective_batch_size(self) -> int:
        return self.batch_size * self.accumulation_steps


# --------------------------------------------------------------------------- small utilities

def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: str | Path, value: Any) -> None:
    """Write JSON beside its target and rename it into place, so a reader never sees half a file."""
    _write_json(Path(path), value)


def _portable_path(path: Path) -> str:
    """A path inside the checkout as a POSIX path relative to its root; any other path as it is."""
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def recorded_evaluation_dir(record: Mapping[str, Any]) -> Path | None:
    """Where a run's reports are, from its record.

    A relative entry is resolved against this checkout, so a run still finds its reports
    after the checkout moved. An absolute entry (a run recorded before paths were kept
    relative, or one whose reports lie outside the checkout) is used as recorded.
    """
    value = record.get("evaluation_dir")
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def set_seed(seed: int) -> None:
    """Seed Python, NumPy and PyTorch. A GPU run still differs slightly between repeats."""
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def git_state(repository: Path | None = None) -> dict[str, Any]:
    """Commit and dirty flag of the checkout; ``None`` values where git cannot say."""
    root = repository or Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=30).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
                                    check=True, timeout=60).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def environment(device: Any) -> dict[str, Any]:
    """Library versions and hardware of this process, for the run record."""
    import torch

    packages = {}
    for name in ("torch", "torchvision", "transformers", "tokenizers", "safetensors", "numpy", "Pillow"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    memory = None
    try:
        import os
        memory = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (ValueError, OSError, AttributeError):
        pass
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "ram_gib": memory, "device": str(device),
            "gpu": torch.cuda.get_device_name(0) if str(device).startswith("cuda") else
            ("Apple Metal (MPS)" if str(device) == "mps" else None),
            "packages": packages}


def new_run_id(seed: int, now: datetime | None = None) -> str:
    """``lineitems-layoutlmv3-<UTC time>-<short commit>-s<seed>``: unique per run and traceable to code."""
    moment = now or datetime.now(timezone.utc)
    commit = git_state()["commit"]
    return f"{MODEL_ID}-layoutlmv3-{moment:%Y%m%dT%H%M%SZ}-{(commit or 'nogit')[:7]}-s{seed}"


# --------------------------------------------------------------------------- model

def build_model(labels: Sequence[str], config: TrainConfig) -> tuple[Any, Any, dict[str, Any]]:
    """The pretrained LayoutLMv3 with a new classification head for ``labels``, and its processor.

    Returns ``(model, processor, loading_info)``. ``loading_info["missing_keys"]`` names the
    tensors the checkpoint did not contain: exactly the new head, which starts random.
    """
    from transformers import AutoModelForTokenClassification, AutoProcessor

    source: dict[str, Any] = {"local_files_only": config.offline}
    if config.revision:
        source["revision"] = config.revision
    processor = AutoProcessor.from_pretrained(config.base_checkpoint, apply_ocr=False, **source)
    model, info = AutoModelForTokenClassification.from_pretrained(
        config.base_checkpoint, num_labels=len(labels), id2label=dict(enumerate(labels)),
        label2id={label: index for index, label in enumerate(labels)}, output_loading_info=True, **source)
    return model, processor, info


_BLOCKS = (
    ("classifier", "classification head", "new, random"),
    ("layoutlmv3.embeddings.x_position", "2-D layout embeddings (x, y, width, height)", "pretrained"),
    ("layoutlmv3.embeddings.y_position", "2-D layout embeddings (x, y, width, height)", "pretrained"),
    ("layoutlmv3.embeddings.h_position", "2-D layout embeddings (x, y, width, height)", "pretrained"),
    ("layoutlmv3.embeddings.w_position", "2-D layout embeddings (x, y, width, height)", "pretrained"),
    ("layoutlmv3.embeddings", "text embeddings (subword, sequence position)", "pretrained"),
    ("layoutlmv3.patch_embed", "image patch embeddings", "pretrained"),
    ("layoutlmv3.cls_token", "image patch embeddings", "pretrained"),
    ("layoutlmv3.pos_embed", "image patch embeddings", "pretrained"),
    ("layoutlmv3.encoder", "transformer encoder (12 layers)", "pretrained"),
)


def parameter_blocks(model: Any) -> list[dict[str, Any]]:
    """Parameter counts of the model's main blocks, with where their starting values come from."""
    totals: Counter[tuple[str, str]] = Counter()
    for name, parameter in model.named_parameters():
        block = next(((title, origin) for prefix, title, origin in _BLOCKS if name.startswith(prefix)),
                     ("normalisation layers between blocks", "pretrained"))
        totals[block] += parameter.numel()
    order = list(dict.fromkeys((title, origin) for _, title, origin in _BLOCKS)) + \
        [("normalisation layers between blocks", "pretrained")]
    return [{"block": title, "parameters": totals[(title, origin)], "starts_from": origin}
            for title, origin in order if totals[(title, origin)]]


def masked_cross_entropy(logits: Any, labels: Any) -> Any:
    """Mean cross-entropy over the positions that carry a label; ``-100`` positions do not count."""
    import torch.nn.functional as F

    return F.cross_entropy(logits.reshape(-1, logits.shape[-1]), labels.reshape(-1), ignore_index=IGNORE_INDEX)


def lr_factor(update: int, total_updates: int, warmup_updates: int) -> float:
    """Learning-rate multiplier for optimiser update number ``update`` (0-based): up linearly, then down to 0."""
    if update < warmup_updates:
        return (update + 1) / warmup_updates
    return max(0.0, (total_updates - update) / max(1, total_updates - warmup_updates))


def _optimizer(model: Any, config: TrainConfig) -> Any:
    """AdamW; biases and normalisation weights are not decayed, as is usual for transformers."""
    import torch

    decay, plain = [], []
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            (plain if name.endswith("bias") or "LayerNorm" in name or ".norm." in name else decay).append(parameter)
    return torch.optim.AdamW([{"params": decay, "weight_decay": config.weight_decay},
                              {"params": plain, "weight_decay": 0.0}], lr=config.learning_rate)


# --------------------------------------------------------------------------- inference

def run_inference(model: Any, dataset: PageWindowDataset, device: Any,
                  batch_size: int = 8) -> tuple[dict[str, tuple[WordPrediction, ...]], float | None]:
    """Label every box of every page in ``dataset``; also the mean masked loss when it carries labels.

    The rule is the serving adapter's: a box takes the prediction at its first subword, in
    the first window that contains it. A box without visible text is reported as ``O`` with
    confidence 0, and a page without windows (unreadable) maps to an empty tuple.
    """
    import torch
    from torch.utils.data import DataLoader

    model.eval()
    id2label = model.config.id2label
    label_ids: list[Any] = []
    scores: list[Any] = []
    loss_sum, labelled = 0.0, 0
    with torch.inference_mode():
        for batch in DataLoader(dataset, batch_size=batch_size, shuffle=False, collate_fn=collate_windows):
            logits = model(**{key: batch[key].to(device) for key in MODEL_INPUTS}).logits.float().cpu()
            probabilities = logits.softmax(-1)
            best = probabilities.max(-1)
            scores.extend(best.values)
            label_ids.extend(best.indices)
            count = int((batch["labels"] != IGNORE_INDEX).sum())
            if count:
                loss_sum += float(masked_cross_entropy(logits, batch["labels"])) * count
                labelled += count
    predictions: dict[str, tuple[WordPrediction, ...]] = {image_id: () for image_id, _ in dataset.skipped}
    for page_index, page in enumerate(dataset.pages):
        words = dataset.words[page_index]
        found: dict[int, WordPrediction] = {}
        for window in dataset.page_windows[page_index]:
            word_index = dataset.windows[window]["word_index"]
            for token in torch.nonzero(word_index >= 0).flatten().tolist():
                word = int(word_index[token])
                if word not in found:
                    found[word] = WordPrediction(words.boxes[words.readable[word]].box_id,
                                                 id2label[int(label_ids[window][token])], float(scores[window][token]))
        if len(found) != len(words.readable):
            raise ValueError(f"{page.image_id}: tokenisation did not cover every text box")
        by_box = {words.readable[word]: prediction for word, prediction in found.items()}
        predictions[page.image_id] = tuple(by_box.get(index) or WordPrediction(box.box_id, UNREAD_LABEL, 0.0)
                                           for index, box in enumerate(words.boxes))
    return predictions, (loss_sum / labelled if labelled else None)


# --------------------------------------------------------------------------- training

def _save_best(model: Any, processor: Any, folder: Path) -> None:
    """Weights and processor in the one layout transformers 4 and 5 both load, replaced atomically."""
    stage = folder.with_name(folder.name + ".tmp")
    shutil.rmtree(stage, ignore_errors=True)
    model.save_pretrained(stage)
    save_processor(processor, stage)
    shutil.rmtree(folder, ignore_errors=True)
    stage.rename(folder)


def save_processor(processor: Any, folder: Path) -> None:
    """Save tokenizer and image processor so that ``AutoProcessor`` of transformers 4 *and* 5 loads them.

    transformers 5 writes ``processor_config.json`` and no ``preprocessor_config.json``, which
    4.x cannot read. Writing the image processor on its own and dropping the combined file
    gives a folder both versions load.
    """
    processor.save_pretrained(folder)
    processor.image_processor.save_pretrained(folder)
    (Path(folder) / "processor_config.json").unlink(missing_ok=True)


def train(model: Any, processor: Any, train_set: PageWindowDataset, validation_set: PageWindowDataset,
          config: TrainConfig, *, labels: Sequence[str], score: Callable[[Mapping[str, Any]], Mapping[str, Any]],
          run_dir: str | Path, evaluation_dir: str | Path | None = None, context: Mapping[str, Any] | None = None,
          resume: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Fine-tune ``model`` and keep the epoch with the best validation entity F1.

    ``score(predictions)`` returns the validation report of
    ``lineitems_eval.validation_report``; its ``entities.f1`` selects the checkpoint. The run
    folder gets ``best/`` (weights and processor), ``history.json``, ``training_log.jsonl``,
    ``config.json``, ``labels.json`` and ``manifest.json`` (the run record, with ``context``
    describing the data). ``last.pt`` holds the optimiser state after every epoch, so that
    ``resume=True`` continues an interrupted run; it is removed when the run completes.
    """
    import torch
    from torch.utils.data import DataLoader

    run_dir = Path(run_dir)
    evaluation_dir = Path(evaluation_dir) if evaluation_dir is not None else None
    last_path = run_dir / "last.pt"
    if run_dir.exists() and not (resume and last_path.is_file()):
        raise FileExistsError(f"{run_dir} already exists; a run is never overwritten (resume needs its last.pt)")
    if len(train_set) == 0 or len(validation_set) == 0:
        raise ValueError("training and validation both need at least one readable page")
    device = select_device(config.device)
    set_seed(config.seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    if evaluation_dir is not None:
        evaluation_dir.mkdir(parents=True, exist_ok=True)

    batches_per_epoch = math.ceil(len(train_set) / config.batch_size)
    updates_per_epoch = math.ceil(batches_per_epoch / config.accumulation_steps)
    total_updates = updates_per_epoch * config.epochs
    warmup_updates = max(1, round(config.warmup_fraction * total_updates))
    model = model.float().to(device)
    optimizer = _optimizer(model, config)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda update: lr_factor(update, total_updates, warmup_updates))

    record: dict[str, Any] = {
        "run_id": run_dir.name, "model_id": MODEL_ID, "status": "running", "registry_status": "candidate",
        "run_dir": str(run_dir), "evaluation_dir": _portable_path(evaluation_dir) if evaluation_dir else None,
        "config": asdict(config), "labels": len(labels), "text_unit_granularity": TEXT_UNIT,
        "base_checkpoint": config.base_checkpoint, "base_revision": config.revision,
        "base_checkpoint_licence": BASE_LICENCE if config.base_checkpoint == BASE_CHECKPOINT else None,
        "parameters": sum(p.numel() for p in model.parameters()),
        "schedule": {"batches_per_epoch": batches_per_epoch, "updates_per_epoch": updates_per_epoch,
                     "total_updates": total_updates, "warmup_updates": warmup_updates,
                     "effective_batch_size": config.effective_batch_size},
        "data": {"train_windows": len(train_set), "train_pages": len(train_set.pages),
                 "train_pages_without_text": [image_id for image_id, _ in train_set.skipped],
                 "validation_windows": len(validation_set), "validation_pages": len(validation_set.pages),
                 "validation_pages_without_text": [image_id for image_id, _ in validation_set.skipped]},
        "selection": "best validation entity F1 (lineitems_eval.entity_scores)", "precision": "float32",
        "environment": environment(device), "code_revision": git_state(), "context": dict(context or {}),
        "created_at": datetime.now(timezone.utc).isoformat(), "seconds": 0.0}
    history: list[dict[str, Any]] = []
    best, stale, start_epoch = -math.inf, 0, 0
    if resume and last_path.is_file():
        state = torch.load(last_path, map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        history, best, stale, start_epoch = state["history"], state["best"], state["stale"], state["epoch"]
        record.update(seconds=state["seconds"], resumed_from_epoch=start_epoch,
                      **{key: state[key] for key in ("best_epoch", "best_validation") if key in state})
        log(f"resuming after epoch {start_epoch}")
    else:
        _write_json(run_dir / "config.json", asdict(config))
        _write_json(run_dir / "labels.json", list(labels))
    _write_json(run_dir / "manifest.json", record)

    try:
        for epoch in range(start_epoch, config.epochs):
            if stale >= config.patience:
                break
            started = time.perf_counter()
            model.train()
            order = torch.Generator().manual_seed(config.seed + epoch)  # the same page order on a resumed epoch
            loader = DataLoader(train_set, batch_size=config.batch_size, shuffle=True, collate_fn=collate_windows,
                                generator=order, num_workers=0)
            optimizer.zero_grad(set_to_none=True)
            loss_sum, labelled = 0.0, 0
            for step, batch in enumerate(loader):
                logits = model(**{key: batch[key].to(device) for key in MODEL_INPUTS}).logits
                loss = masked_cross_entropy(logits, batch["labels"].to(device))
                if not bool(torch.isfinite(loss)):
                    raise FloatingPointError("non-finite training loss")
                group_start = (step // config.accumulation_steps) * config.accumulation_steps
                (loss / min(config.accumulation_steps, len(loader) - group_start)).backward()
                if (step + 1) % config.accumulation_steps == 0 or step + 1 == len(loader):
                    torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                count = int((batch["labels"] != IGNORE_INDEX).sum())
                loss_sum += float(loss.detach()) * count
                labelled += count
            train_seconds = time.perf_counter() - started
            predictions, validation_loss = run_inference(model, validation_set, device, config.eval_batch_size)
            report = score(predictions)
            entity_f1 = report["entities"]["f1"]
            if entity_f1 is None:
                raise ValueError("validation has no scorable field; a checkpoint cannot be selected")
            epoch_record = {
                "epoch": epoch + 1, "train_loss": loss_sum / max(1, labelled), "validation_loss": validation_loss,
                "validation_entity_f1": entity_f1, "validation_entity_precision": report["entities"]["precision"],
                "validation_entity_recall": report["entities"]["recall"],
                "validation_field_micro_f1": report["boxes"]["field_micro"]["f1"],
                "validation_field_macro_f1": report["boxes"]["field_macro_f1"],
                "validation_box_accuracy": report["boxes"]["accuracy_all_scored"],
                "learning_rate": scheduler.get_last_lr()[0], "train_seconds": train_seconds,
                "seconds": time.perf_counter() - started}
            history.append(epoch_record)
            record["seconds"] += epoch_record["seconds"]
            improved = entity_f1 > best + config.min_delta
            if improved:
                best, stale = entity_f1, 0
                _save_best(model, processor, run_dir / "best")
                record.update(best_epoch=epoch + 1, best_validation={
                    "entity_f1": entity_f1, "field_micro_f1": epoch_record["validation_field_micro_f1"],
                    "field_macro_f1": epoch_record["validation_field_macro_f1"], "loss": validation_loss})
                if evaluation_dir is not None:
                    _write_json(evaluation_dir / "val_metrics.json", {"split": "validation", "epoch": epoch + 1,
                                                                      "provenance": "synthetic", **report})
            else:
                stale += 1
            record["completed_epochs"] = len(history)
            _write_json(run_dir / "history.json", history)
            with (run_dir / "training_log.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(epoch_record) + "\n")
            torch.save({"model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                        "optimizer": optimizer.state_dict(), "scheduler": scheduler.state_dict(), "epoch": epoch + 1,
                        "history": history, "best": best, "stale": stale, "seconds": record["seconds"],
                        "best_epoch": record.get("best_epoch"), "best_validation": record.get("best_validation")},
                       last_path)
            _write_json(run_dir / "manifest.json", record)
            log(f"epoch {epoch + 1:>2}/{config.epochs}  train loss {epoch_record['train_loss']:.4f}  "
                f"val loss {validation_loss:.4f}  val entity F1 {entity_f1:.4f}  "
                f"field micro-F1 {epoch_record['validation_field_micro_f1']:.4f}  "
                f"lr {epoch_record['learning_rate']:.2e}  {epoch_record['seconds']:.0f}s"
                f"{'  <- best so far' if improved else ''}")
            if device.type == "mps":
                torch.mps.empty_cache()
        stopped_early = len(history) < config.epochs
        record.update(status="completed", stopped_early=stopped_early,
                      weights_sha256=sha256_file(run_dir / "best" / "model.safetensors"))
        last_path.unlink(missing_ok=True)
    except BaseException as error:
        record.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(run_dir / "manifest.json", record)
        if evaluation_dir is not None:
            _write_json(evaluation_dir / "manifest.json", record)
    return record


def load_run(run_dir: str | Path, device: Any = "cpu") -> tuple[Any, Any, list[str], dict[str, Any]]:
    """The best checkpoint of a saved run, its processor, its labels and its run record. No retraining."""
    from transformers import AutoModelForTokenClassification, AutoProcessor

    run_dir = Path(run_dir)
    if not (run_dir / "best" / "model.safetensors").is_file():
        raise FileNotFoundError(f"no saved best checkpoint in {run_dir}")
    record = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    labels = json.loads((run_dir / "labels.json").read_text(encoding="utf-8"))
    processor = AutoProcessor.from_pretrained(str(run_dir / "best"), apply_ocr=False, local_files_only=True)
    model = AutoModelForTokenClassification.from_pretrained(str(run_dir / "best"), local_files_only=True)
    if [model.config.id2label[index] for index in range(len(labels))] != labels:
        raise ValueError(f"{run_dir}: the checkpoint's labels are not the run's labels")
    return model.to(select_device(device) if isinstance(device, str) else device).eval(), processor, labels, record


def evaluate_pages(model: Any, processor: Any, pages: Sequence[CachedPage],
                   annotations: Mapping[str, Mapping[str, Any]], policy: LabelPolicy, labels: Sequence[str], *,
                   device: Any, batch_size: int = 8) -> dict[str, Any]:
    """Align, label and score the given pages in one call: the same code for validation and for the final test.

    Returns ``alignments``, ``predictions``, ``loss`` and the ``report`` of
    ``lineitems_eval.validation_report``. Pages M4 could not read stay in every denominator.
    """
    from pipelines.documents.lineitems_data import align_pages
    from pipelines.documents.lineitems_eval import validation_report

    alignments = align_pages(pages, annotations, policy)
    dataset = PageWindowDataset(list(pages), processor, alignments=alignments,
                                label2id={label: index for index, label in enumerate(labels)})
    predictions, loss = run_inference(model, dataset, device, batch_size)
    return {"alignments": alignments, "predictions": predictions, "loss": loss, "dataset": dataset,
            "report": validation_report(alignments, predictions, annotations)}


# --------------------------------------------------------------------------- guards for a notebook execution

def evaluation_folder_name(run_id: str, *, quick_run: bool, reloaded: Mapping[str, Any] | None) -> str:
    """Name of the folder under ``artifacts/evaluation`` that one notebook execution writes its reports to.

    It is the run's own folder, with one exception: a quick execution that reloads a run
    which was not itself a quick run writes to ``<run_id>-quick-check``. Scores of a few
    pages can then never replace the full run's reports.
    """
    reloaded_full_run = reloaded is not None and not (reloaded.get("context") or {}).get("quick_run")
    return f"{run_id}-quick-check" if quick_run and reloaded_full_run else run_id


def reload_mismatches(record: Mapping[str, Any], policy: LabelPolicy, cache: M4Cache) -> list[str]:
    """Why a saved run cannot be evaluated with the present inputs; empty when it can.

    A run's scores are tied to the labels and the M4 output it was trained on. Evaluating it
    under another label policy, or on another M4 cache, would compare it with other truth.
    """
    context = record.get("context") or {}
    problems: list[str] = []
    saved, current = context.get("label_policy") or {}, policy.to_dict()
    if saved != current:
        differing = sorted(key for key in set(saved) | set(current) if saved.get(key) != current.get(key))
        problems.append("label policy: the run was labelled with "
                        + ", ".join(f"{key}={saved.get(key)!r}" for key in differing) + "; this notebook now uses "
                        + ", ".join(f"{key}={current.get(key)!r}" for key in differing))
    saved_hash = (context.get("m4_cache") or {}).get("manifest_sha256")
    manifest_path = cache.root / "manifest.json"
    current_hash = sha256_file(manifest_path)
    if saved_hash != current_hash:
        problems.append(f"M4 cache: the run was trained on the cache with manifest sha256 {saved_hash}; "
                        f"{manifest_path} has {current_hash}")
    return problems


def open_final_evaluation(evaluation_dir: str | Path, *, run_id: str, quick_run: bool) -> Path:
    """Reserve the run's test report before any held-out page is read; returns the path to write it to.

    The held-out split is opened once per run. This creates ``test_metrics.json`` with status
    ``opened`` and refuses when the file is already there, whether a finished report or an
    opening that did not finish. A quick run is refused before anything is created: a
    plumbing check must not spend the test split.
    """
    path = Path(evaluation_dir) / TEST_REPORT_NAME
    if quick_run:
        raise RuntimeError("FINAL_EVAL is refused while QUICK_RUN is on: a plumbing check on a few pages must not "
                           "open the held-out test split. Set QUICK_RUN = False first.")
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = {"split": "test", "provenance": "synthetic", "run_id": run_id, "status": "opened",
              "opened_at": datetime.now(timezone.utc).isoformat()}
    try:
        with path.open("x", encoding="utf-8") as stream:  # "x": fails when the file exists, without a race
            stream.write(json.dumps(marker, indent=2) + "\n")
    except FileExistsError:
        raise RuntimeError(
            f"The held-out test split was already opened for this run: {path} exists. A test split that is "
            "scored again after looking at its result is no longer held out, so the final evaluation is not "
            "repeated. Read that file instead.") from None
    return path


# --------------------------------------------------------------------------- the rows M6 receives

def rows_for_m6(pages: Sequence[CachedPage], results: Mapping[str, Any], *, split: str, quick_run: bool,
                model_version: str, registry_entry: str | None, m4_cache_manifest_sha256: str, family_id: str,
                min_confidence: float, produced_by: str) -> dict[str, Any]:
    """What M6 is given by M5 for the estimate pages of ``pages`` (schema ``cmev-m5-rows-for-m6/1``).

    ``results`` maps a dataset claim id to the ``ParseResult`` of ``adapt_predictions`` for
    that claim. Every estimate page gets one entry holding the ``row_boxes`` of
    ``cmev.cmd.pen-marks-detect.v1`` for its line items (``pen_mark_row_boxes``): a row box
    is the union of the text boxes M5 put into the row, not the printed table band. A page
    M4 could not read, or one without line items, is listed with no rows: M6 then has no
    row to link a mark to, which is different from the page being absent.
    """
    listed = []
    for page in pages:
        if page.page_type != "repair_estimate":
            continue
        boxes = pen_mark_row_boxes(results[page.dataset_claim_id])
        listed.append({"image_id": page.image_id, "page_id": page.page.page_id, "claim_id": page.claim_id,
                       "dataset_claim_id": page.dataset_claim_id, "page_status": page.page_status,
                       "row_boxes": [row for row in boxes if row["page_id"] == page.page.page_id]})
    return {"schema": ROWS_FOR_M6_SCHEMA, "split": split, "provenance": "synthetic", "quick_run": bool(quick_run),
            "m5_model_version": model_version, "m5_registry_entry": registry_entry,
            "m4_cache_manifest_sha256": m4_cache_manifest_sha256, "family_id": family_id,
            "min_confidence": min_confidence, "produced_by": produced_by, "pages": listed}


def registry_entry_with_weights(registry_root: str | Path, model_version: str, served_version: str) -> str | None:
    """``model_version`` when that registry entry exists and holds the weights ``served_version`` names, else ``None``."""
    manifest_path = Path(registry_root) / model_version / "manifest.json"
    if not manifest_path.is_file():
        return None
    recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("weights_sha256")
    return model_version if recorded and "sha256:" + recorded == served_version else None


# --------------------------------------------------------------------------- data description

def describe_training_data(cache: M4Cache, alignments: Mapping[str, PageAlignment], policy: LabelPolicy,
                           labels: Sequence[str]) -> dict[str, Any]:
    """What a run was trained on: dataset and M4 cache identity, the label policy and per-split counts.

    Label statistics cover the aligned pages only. A split that was not aligned (the test
    split, during development) is listed with its page ids and no label counts.
    """
    manifest = cache.manifest
    splits: dict[str, Any] = {}
    for name in sorted({page.split for page in cache.pages}):
        pages = cache.split(name)
        ids = sorted(page.image_id for page in pages)
        entry: dict[str, Any] = {
            "pages": len(pages), "claims": len({page.dataset_claim_id for page in pages}),
            "pages_unreadable": sum(page.page_status == "unreadable" for page in pages),
            "image_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}
        aligned = [alignments[image_id] for image_id in ids if image_id in alignments]
        if aligned:
            boxes = [box for alignment in aligned for box in alignment.boxes]
            fields = [match for alignment in aligned for match in alignment.fields]
            entry.update(
                aligned_pages=len(aligned), text_boxes=len(boxes),
                boxes_by_outcome=dict(sorted(Counter(box.outcome for box in boxes).items())),
                fields_by_status=dict(sorted(Counter(match.status for match in fields).items())),
                boxes_per_label=dict(sorted(Counter(box.label for box in boxes if box.label is not None).items())))
        else:
            entry["label_statistics"] = None
            entry["label_statistics_reason"] = "split not aligned in this run; the test split stays unopened"
        splits[name] = entry
    return {
        "dataset": {**dict(manifest["dataset"]), "provenance": "synthetic"},
        "m4_cache": {"schema": manifest["schema"], "manifest_sha256": sha256_file(cache.root / "manifest.json"),
                     "versions": dict(manifest["m4"]["versions"] or {}),
                     "m4_source_sha256": manifest["m4"].get("m4_source_sha256"),
                     "git_commit": manifest["m4"].get("git_commit"), "git_dirty": manifest["m4"].get("git_dirty"),
                     "container_image": manifest["m4"].get("container_image"),
                     "text_granularity": TEXT_UNIT},
        "label_policy": policy.to_dict(),
        "labels": {"count": len(labels), "scheme": "O plus B-/I- per dataset field label, at text-box level"},
        "grouping_key": "dataset claim id (two pages per claim always share a split)",
        "splits": splits}


# --------------------------------------------------------------------------- export for serving

def _entry_name(model_version: str) -> None:
    name = PurePosixPath(model_version)
    if len(name.parts) != 2 or name.parts[0] != MODEL_ID or name.parts[1] in {".", ".."}:
        raise ValueError(f"model_version must be {MODEL_ID}/<name>, not {model_version!r}")


def _notice(record: Mapping[str, Any], model_version: str) -> str:
    context = record["context"]
    return f"""# Model notice: {model_version}

Candidate M5 line-item labeller (LayoutLMv3 token classification over M4 text boxes).
Status `candidate`: trained and scored on synthetic pages only, not accepted by the lane owner.

## Base checkpoint
- `{record['base_checkpoint']}`, revision `{record['base_revision']}`.
- Model-card licence: `{BASE_LICENCE}` (Creative Commons Attribution-NonCommercial-ShareAlike 4.0).
  **Non-commercial use only; derivatives, including these fine-tuned weights, carry the same terms.**

## Training data
- `{context['dataset']['name']}`: fully synthetic pages made by this team (one page layout, font-rendered
  surveyor ink). No third-party data, no personal data, no access agreement.
- Inputs are the output of the M4 page-reading module on those pages (M4 cache manifest
  `{context['m4_cache']['manifest_sha256']}`), read with PaddleOCR
  (`{context['m4_cache']['versions'].get('ocr_weights')}`). The OCR models' own licences apply to M4, not to these weights.

## Limits
Synthetic validation evidence only. Nothing here establishes accuracy on real estimates, on another
layout or on real handwriting. Model proposals are proposals: declaration completeness stays `partial`.
"""


def export_for_serving(run_dir: str | Path, model_version: str, registry_root: str | Path, *,
                       check_pages: Sequence[CachedPage], created_by: str, known_limits: str,
                       targets: Mapping[str, Any] | None = None, device: str = "cpu",
                       log: Callable[[str], None] = print) -> Path:
    """Write the best checkpoint of ``run_dir`` as registry entry ``<registry_root>/<model_version>``.

    The entry is staged beside its destination and checked before it appears: every required
    file is present, the weight hash matches the manifest, no manifest field is silently
    absent, and ``LocalLayoutLMv3`` (the application's loader) loads the staged folder and
    gives, on every page of ``check_pages``, exactly the labels the run's model gives.
    """
    import yaml

    _entry_name(model_version)
    run_dir, entry = Path(run_dir), Path(registry_root) / model_version
    if entry.exists():
        raise FileExistsError(f"{entry} already exists; a registry entry is not replaced, choose another version")
    if not check_pages:
        raise ValueError("check_pages is empty: the export is verified on real M4 pages, not assumed")
    model, processor, labels, record = load_run(run_dir, device)
    if record.get("status") != "completed":
        raise ValueError(f"run {run_dir.name} is {record.get('status')!r}; only a completed run is exported")
    context = record["context"]
    evaluation_dir = recorded_evaluation_dir(record)
    report_path = evaluation_dir / "val_metrics.json" if evaluation_dir is not None else None
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path and report_path.is_file() else None
    if report is None:
        raise FileNotFoundError(f"the run has no validation report ({report_path or 'no evaluation folder recorded'}); "
                                "nothing is exported unscored")

    entry.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".export-", dir=entry.parent) as temporary:
        stage = Path(temporary) / entry.name
        model.save_pretrained(stage)
        save_processor(processor, stage)
        weights_sha256 = sha256_file(stage / "model.safetensors")
        image = processor.image_processor
        size = image.size if isinstance(image.size, Mapping) else {"height": image.size.height, "width": image.size.width}
        preprocessing = {
            "preprocessing_version": PREPROCESSING_VERSION, **serving_preprocessing(TEXT_UNIT),
            "text_source": "TextBox.text of the M4 DocumentPage, one box per unit, in TextBox.order_index order",
            "bbox_source": "TextBox.box_norm (0..1 of the M4 corrected page)", "bbox_rounding": "truncate",
            "bbox_clip": [0, 999], "unreadable_box_rule": "a box without visible text gets no token and is "
                                                          "reported as O with confidence 0",
            "image": {"source": "M4 corrected page (rectified image)", "color_space": "RGB",
                      "size": [int(size["height"]), int(size["width"])],
                      "resize_policy": "stretch to a square, aspect ratio not kept", "resample": int(image.resample),
                      "rescale_factor": float(image.rescale_factor),
                      "mean": [float(v) for v in image.image_mean], "std": [float(v) for v in image.image_std]},
            "output_coordinates": "predictions name TextBox.box_id; geometry is the M4 record's, never the model's",
            "label_alignment_policy": context["label_policy"], "adapter_alignment_version": ALIGNMENT_VERSION}
        _write_json(stage / "preprocessing.json", preprocessing)
        _write_json(stage / "label_schema.json", {
            "label_schema_version": LABEL_SCHEMA_VERSION, "scheme": "BIO over M4 text boxes",
            "outside_label": "O", "outside_index": labels.index("O"), "num_labels": len(labels),
            "id_to_label": {str(index): label for index, label in enumerate(labels)},
            "row_slot_labels": "part_N_<field> names the Nth printed table row of a page; N is page-local and "
                               "never a part code or a global entry id",
            "taxonomy_version": None,
            "taxonomy_version_reason": "labels are dataset field names; part and operation codes are mapped later "
                                       "by the estimate vocabulary in adapt_predictions",
            "side_resolution": "not applicable to field labels; printed side text is read by the vocabulary"})
        split_manifest = {
            "split_version": f"{context['dataset']['name']}@manifest.csv:{context['dataset']['manifest_sha256'][:12]}",
            "source": "manifest.csv of the dataset (inherited claim-grouped split)",
            "grouping_key": context["grouping_key"], "partitions": context["splits"],
            "split_hashes": {name: split["image_ids_sha256"] for name, split in context["splits"].items()},
            "split_hash_definition": "sha256 of the partition's sorted image ids joined by newlines",
            "dataset_manifest_sha256": context["dataset"]["manifest_sha256"],
            "dataset_annotations_sha256": context["dataset"]["annotations_sha256"],
            "m4_cache_manifest_sha256": context["m4_cache"]["manifest_sha256"]}
        _write_json(stage / "split_manifest.json", split_manifest)
        _write_json(stage / "evaluation_report.json", {
            "partition": "validation", "split_version": split_manifest["split_version"], "provenance": "synthetic",
            "selected_epoch": record.get("best_epoch"),
            "statement": "Scores of the selected epoch on the validation split of synthetic pages. The test "
                         "split was not used. Not evidence about real estimates.",
            "shortfall": None, "shortfall_reason": "no project target is evaluated by this report; see targets "
                                                   "in manifest.json",
            "report": report})
        log_lines = (run_dir / "training_log.jsonl").read_text(encoding="utf-8")
        (stage / "training_log.jsonl").write_text(
            log_lines + json.dumps({"final_effective_config": record["config"]}) + "\n", encoding="utf-8")
        (stage / "config.yaml").write_text(yaml.safe_dump({
            "training": record["config"], "seed": record["config"]["seed"], "schedule": record["schedule"],
            "label_policy": context["label_policy"], "labels": len(labels),
            "base_checkpoint": record["base_checkpoint"], "base_revision": record["base_revision"],
            "text_unit_granularity": TEXT_UNIT}, sort_keys=False), encoding="utf-8")
        (stage / "NOTICE.md").write_text(_notice(record, model_version), encoding="utf-8")
        env = record["environment"]
        manifest = {
            "model_id": MODEL_ID, "version": model_version, "status": "candidate",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "created_by": created_by, "task": "token_classification", "architecture": "layoutlmv3-base",
            "base_checkpoint": f"{record['base_checkpoint']}@{record['base_revision']}",
            "base_checkpoint_licence": f"{BASE_LICENCE} (non-commercial, share-alike)",
            "weights_sha256": weights_sha256, "label_schema_version": LABEL_SCHEMA_VERSION,
            "taxonomy_version": None,
            "taxonomy_version_reason": "field labels are dataset field names, not taxonomy codes",
            "preprocessing_version": PREPROCESSING_VERSION,
            "dataset_ids": [context["dataset"]["name"]],
            "dataset_ids_note": "team-generated; not yet listed in data/manifests/dataset_sources.json",
            "dataset_access_evidence": None,
            "dataset_access_evidence_reason": "fully synthetic pages generated by the team; no third-party access terms",
            "split_version": split_manifest["split_version"], "split_hashes": split_manifest["split_hashes"],
            "conversion_version": {"label_alignment_policy": context["label_policy"],
                                   "overlap_policy": "a box overlapping two fields, or a field and handwriting, is masked",
                                   "background_policy": "a box overlapping no field region is O"},
            "seed": record["config"]["seed"],
            "hardware": {key: env.get(key) for key in ("device", "gpu", "machine", "processor", "ram_gib", "platform")}
            | {"host": "team machine"},
            "dependency_versions": env["packages"], "code_revision": record["code_revision"],
            "training_duration_s": round(record["seconds"], 1),
            "metrics": {"partition": "validation", "provenance": "synthetic", "selected_epoch": record.get("best_epoch"),
                        "pages": report["pages"], "pages_unreadable": report["pages_unreadable"],
                        "entities": report["entities"], "boxes": report["boxes"], "rows": report["rows"]},
            "targets": dict(targets or {}), "known_limits": known_limits, "provenance": "synthetic",
            "contract_version": SCHEMA_VERSION,
            "text_unit_granularity": TEXT_UNIT, "m4_cache": context["m4_cache"],
            "source_run": {"run_id": record["run_id"], "completed_epochs": record.get("completed_epochs"),
                           "best_epoch": record.get("best_epoch"), "stopped_early": record.get("stopped_early"),
                           "parameters": record.get("parameters"), "schedule": record["schedule"]}}
        _write_json(stage / "manifest.json", manifest)

        problems = entry_problems(stage)
        if problems:
            raise RuntimeError("the staged entry is incomplete; nothing was exported: " + "; ".join(problems))
        served = LocalLayoutLMv3(stage, device)
        if served.text_unit != TEXT_UNIT or served.model_version != "sha256:" + weights_sha256:
            raise RuntimeError("the serving loader read another text unit or other weights; nothing was exported")
        expected, _ = run_inference(model, PageWindowDataset(list(check_pages), processor), select_device(device))
        compared = 0
        for page in check_pages:
            got = served.predict(page.page, page.rectified_path)
            want = expected[page.image_id]
            if [(p.box_id, p.label) for p in got] != [(p.box_id, p.label) for p in want] or any(
                    abs(a.confidence - b.confidence) > 1e-4 for a, b in zip(got, want)):
                raise RuntimeError(f"the serving loader does not reproduce the run's labels on {page.image_id}; "
                                   "nothing was exported")
            compared += len(got)
        log(f"serving loader reproduced {compared} box labels on {len(check_pages)} page(s)")
        shutil.move(str(stage), str(entry))
    return entry


def entry_problems(entry: str | Path) -> list[str]:
    """Why a folder is not a complete registry entry (training specification 10.2, 10.3 and 12); empty if it is."""
    entry = Path(entry)
    problems = [f"missing {name}" for name in REQUIRED_ENTRY_FILES if not (entry / name).is_file()]
    if problems:
        return problems
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    for name in MANIFEST_FIELDS:
        if name not in manifest:
            problems.append(f"manifest field {name} is absent")
        elif manifest[name] is None and not manifest.get(f"{name}_reason"):
            problems.append(f"manifest field {name} is null without {name}_reason")
    if manifest.get("weights_sha256") != sha256_file(entry / "model.safetensors"):
        problems.append("weights_sha256 does not match model.safetensors")
    if manifest.get("status") != "candidate":
        problems.append("a training export must have status candidate")
    preprocessing = json.loads((entry / "preprocessing.json").read_text(encoding="utf-8"))
    for key, value in serving_preprocessing(TEXT_UNIT).items():
        if preprocessing.get(key) != value:
            problems.append(f"preprocessing.json {key} is {preprocessing.get(key)!r}, the adapter applies {value!r}")
    return problems


__all__ = ["BASE_CHECKPOINT", "BASE_LICENCE", "BASE_REVISION", "MANIFEST_FIELDS", "MODEL_ID", "REPOSITORY_ROOT",
           "REQUIRED_ENTRY_FILES", "ROWS_FOR_M6_SCHEMA", "TEST_REPORT_NAME", "TEXT_UNIT", "TrainConfig", "build_model",
           "describe_training_data", "entry_problems", "environment", "evaluate_pages", "evaluation_folder_name",
           "export_for_serving", "git_state", "load_run", "lr_factor", "masked_cross_entropy", "new_run_id",
           "open_final_evaluation", "parameter_blocks", "recorded_evaluation_dir", "registry_entry_with_weights",
           "reload_mismatches", "rows_for_m6", "run_inference", "save_processor", "set_seed", "train",
           "write_json_atomic"]
