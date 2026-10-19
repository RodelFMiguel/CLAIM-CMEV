"""Standalone headless training script for SegFormer-B3 reflection-resistant damage segmentation.

Trains SegFormer-B3 (nvidia/segformer-b3-finetuned-ade-512-512) on vehicle damage
with compound loss, EMA weight averaging, damage-aware cropping, and specular glare
reflection augmentation to eliminate false positive dent predictions on reflective sheet metal.

Usage:
    .venv/bin/python pipelines/vision/train_segformer_b3.py [--epochs 60] [--batch-size 2] [--device cuda]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from claim_cmev.vision.parts.config import load_parts_config
from claim_cmev.vision.damage.model_config import load_damage_model_config
from pipelines.vision.hitl_data import load_prepared
from pipelines.vision.registry import validate_serving_training_config, export_damage_run
from pipelines.vision.training import TrainingConfig, train, evaluate_run, plot_history

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("cmev.pipelines.train_segformer_b3")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train SegFormer-B3 Damage Segmentation")
    parser.add_argument("--epochs", type=int, default=60, help="Number of training epochs")
    parser.add_argument("--patience", type=int, default=10, help="Early stopping patience")
    parser.add_argument("--batch-size", type=int, default=2, help="Per-device batch size")
    parser.add_argument("--accumulation-steps", type=int, default=4, help="Gradient accumulation steps")
    parser.add_argument("--device", type=str, default="cuda", help="Device (cuda or cpu)")
    parser.add_argument("--task", type=str, default="damage_cardd", choices=["damage_cardd", "damage"], help="Task vocabulary to train")
    parser.add_argument("--specular-reflection", type=float, default=0.40, help="Specular reflection augmentation probability")
    parser.add_argument("--run-id", type=str, default=None, help="Explicit run ID")
    parser.add_argument("--export", action="store_true", default=True, help="Export candidate to registry after training")
    args = parser.parse_args()

    if args.task == "damage_cardd":
        data_dir = PROJECT_ROOT / "data/processed/cardd/v1"
        if not (data_dir / "manifest.json").is_file():
            raise FileNotFoundError(f"Prepared CarDD data not found at {data_dir}. Run prepare_cardd first.")
    else:
        data_dir = PROJECT_ROOT / "data/processed/hitl/v3"
        if not (data_dir / "manifest.json").is_file():
            raise FileNotFoundError(f"Prepared HITL v3 data not found at {data_dir}. Run prepare_hitl first.")

    log.info(f"Loading prepared data from {data_dir}...")
    manifest = load_prepared(data_dir)

    raw_classes = manifest["class_names"]
    class_names = raw_classes[args.task] if isinstance(raw_classes, dict) else raw_classes
    log.info(f"Task: {args.task} | Taxonomy ({len(class_names)} classes): {class_names}")

    if args.task == "damage_cardd":
        focus_names = ["scratch", "crack"]
    else:
        focus_names = ["flaking", "paint-chip", "scratch", "cracked"]

    config = TrainingConfig(
        task=args.task,
        architecture="segformer",
        checkpoint="nvidia/segformer-b3-finetuned-ade-512-512",
        image_size=512,
        frame="serving",
        batch_size=args.batch_size,
        accumulation_steps=args.accumulation_steps,
        epochs=args.epochs,
        patience=args.patience,
        encoder_lr=6e-5,
        head_lr=6e-4,
        weight_decay=0.05,
        lr_schedule="plateau",
        warmup_epochs=min(2, max(0, args.epochs - 1)),
        plateau_patience=3,
        plateau_factor=0.5,
        plateau_grace_epochs=3,
        freeze_batchnorm=False,
        freeze_encoder_epochs=0,
        drop_path_rate=0.2,
        classifier_dropout=0.2,
        ema_decay=0.995,
        ce_weight=1.0,
        dice_weight=0.5,
        class_weighting="sqrt_inverse",
        class_weight_cap=10.0,
        crop_mode="damage_aware",
        full_image_probability=0.25,
        focused_crop_probability=0.50,
        crop_scale_range=(1.0, 1.5),
        crop_focus_class_ids=tuple(
            class_names.index(name) for name in focus_names if name in class_names
        ),
        repeat_factor_threshold=0.20,
        max_repeat_factor=3.0,
        horizontal_flip=0.5,
        brightness=0.15,
        contrast=0.15,
        saturation=0.10,
        specular_reflection=args.specular_reflection,
        amp=True,
        seed=20260922,
        device=args.device,
        artifacts_root=str(PROJECT_ROOT / "artifacts"),
        run_id=args.run_id,
    )

    log.info("Validating serving training configuration...")
    parts_cfg = load_parts_config(PROJECT_ROOT / "configs/models/parts.yaml")
    damage_cfg = load_damage_model_config(PROJECT_ROOT / "configs/models/damage.yaml")
    validate_serving_training_config(config, parts_config=parts_cfg, damage_config=damage_cfg)

    log.info(f"Starting training SegFormer-B3 ({config.checkpoint}) for {args.task} on {args.device}...")
    run_record = train(manifest, config)
    run_dir = Path(run_record["run_dir"])
    log.info(f"Training completed successfully. Run artifacts saved to {run_dir}")

    # Generate performance plot
    try:
        plot_history(run_dir)
        log.info(f"Saved history plots to {run_record['evaluation_dir']}")
    except Exception as exc:
        log.warning(f"Could not generate history plot: {exc}")

    # Evaluate validation split
    log.info("Evaluating final model on validation split (512x512, background_offset=0)...")
    val_metrics = evaluate_run(run_dir, manifest, split="val", device=args.device, image_size=512)
    log.info(f"Validation Foreground mIoU: {val_metrics.get('foreground_mIoU', 'N/A')}")
    log.info(f"Per-class IoU: {val_metrics.get('per_class_iou')}")

    if args.export:
        export_name = f"{run_dir.name}-serving"
        model_prefix = "damage-cardd" if args.task == "damage_cardd" else "damage-hitl"
        model_version = f"{model_prefix}/{export_name}"
        log.info(f"Exporting candidate model to registry: {model_version}...")
        exported_path = export_damage_run(run_dir, model_version=model_version)
        log.info(f"Candidate exported successfully to {exported_path}")
        log.info("To serve this checkpoint, update configs/models/damage.yaml with:")
        log.info(f"  model_id: {model_prefix}")
        log.info(f"  model_version: {model_version}")
        log.info(f"  architecture: nvidia/segformer-b3-finetuned-ade-512-512")
        log.info(f"  num_labels: {len(class_names)}")


if __name__ == "__main__":
    main()
