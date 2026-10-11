"""Execute notebook configuration cells without data, model downloads or fitting."""
from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")
from claim_cmev.contracts.common import DAMAGE_CODES, HITL_DAMAGE_CODES
from claim_cmev.vision.palette import ID_TO_PART_CODE
from claim_cmev.vision.parts.config import load_parts_config
from claim_cmev.vision.damage.model_config import load_damage_model_config
from pipelines.vision.registry import validate_serving_training_config
from pipelines.vision.training import TrainingConfig

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ("M1_HITL_parts_training.ipynb", "parts", list(ID_TO_PART_CODE.values())),
    ("M2_HITL_damage_training.ipynb", "damage", ["background", *HITL_DAMAGE_CODES]),
    ("M2_CarDD_damage_training.ipynb", "damage_cardd", ["background", *DAMAGE_CODES]),
]


@pytest.mark.parametrize("name,task,classes", CASES)
def test_notebook_defaults_are_exportable_and_evaluate_without_an_unserved_offset(name, task, classes, tmp_path):
    notebook = json.loads((ROOT / "notebooks" / name).read_text())
    codes = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
    for index, code in enumerate(codes):
        compile(code, f"{name}:cell{index}", "exec")
    env = dict(ROOT=ROOT, TASK=task, class_names=classes, DEVICE="cpu", SPLIT_SEED=20260922,
               TrainingConfig=TrainingConfig, asdict=asdict, json=json, pd=pd, records=[], train_records=[],
               class_loss_weights=lambda *args: np.ones(len(classes)), display=lambda *args: None)
    exec(next(c for c in codes if "cfg = TrainingConfig(" in c), env)
    cfg = env["cfg"]
    assert cfg.frame == "serving" and cfg.image_size == 512 and cfg.architecture == "segformer"
    assert env["TRAIN_FOR_SERVING"] is True
    validate_serving_training_config(cfg)
    env.update(RUN_DIR=tmp_path, EVALUATION_DIR=tmp_path, manifest={})
    calls = []
    env.update(evaluate_run=lambda *a, **kw: calls.append(kw), compare_runs=lambda *a, **kw: pd.DataFrame())
    if task != "parts":
        exec(next(c for c in codes if "COMMON_EVAL_SIZE =" in c), env)
        assert calls == [{"split": "val", "device": "cpu", "image_size": 512}]
        exec(next(c for c in codes if "TUNE_BACKGROUND_OFFSET =" in c), env)
        assert env["TUNE_BACKGROUND_OFFSET"] is False and env["OFFSET_EVAL_SIZE"] == 512
        env["BACKGROUND_OFFSET"] = 2.0  # A prior experiment must not leak into serving-route test evaluation.
        exec(next(c for c in codes if "FINAL_EVAL =" in c), env)
        assert env["FINAL_BACKGROUND_OFFSET"] == 0.0 and env["FINAL_EVAL_SIZE"] == 512
        assert len(calls) == 1  # Final test remains opt-in.
    # Users save this switch enabled after training; exercise the disabled path
    # without exporting their real checkpoint or changing the saved notebook.
    export_code = next(c for c in codes if "EXPORT_FOR_SERVING =" in c)
    exec(export_code.replace("EXPORT_FOR_SERVING = True", "EXPORT_FOR_SERVING = False"), env)
    assert env["EXPORT_FOR_SERVING"] is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("overrides,reason", [
    ({"frame": "centred"}, "frame"),
    ({"image_size": 640}, "input_size"),
    ({"mean": (0.5, 0.5, 0.5)}, "pixel_mean"),
    ({"std": (0.5, 0.5, 0.5)}, "pixel_std"),
    ({"architecture": "resnet50"}, "SegFormer"),
])
def test_preflight_refuses_a_recipe_that_would_fail_export(overrides, reason):
    config = replace(TrainingConfig(task="damage", frame="serving"), **overrides)
    with pytest.raises(ValueError, match=reason):
        validate_serving_training_config(config)


def test_preflight_refuses_mismatched_m1_m2_frames():
    with pytest.raises(ValueError, match="M1 and M2"):
        validate_serving_training_config(TrainingConfig(task="damage_cardd", frame="serving"),
            parts_config=load_parts_config().model_copy(update={"input_size": 640}),
            damage_config=load_damage_model_config())
