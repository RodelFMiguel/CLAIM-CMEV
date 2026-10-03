"""Bounded LightGBM quantile ranges: the learned M7 method (training specification 7.4.2).

Two regressors with quantile loss give a lower and an upper bound per cost key from three
categorical features: part, operation and vehicle class. Currency and basis are constant
and recorded, never features; side, damage type, model year, workshop and date never enter.

The training unit is the independent base case (the median of its quotes), exactly as in
the empirical method, so support means the same thing in both. The target is the natural
log of that amount, because the price structure and the conformal offset are multiplicative.
Floats exist only inside LightGBM: a prediction becomes a fixed-place ``Decimal`` before it
is a bound, and bounds are rounded once at publication like every other ``KeyFit``.

The model pools across keys, so it could predict a key with little or no data. It is not
allowed to serve one: support is still the count of distinct train base cases, and the
shared support rule withholds a key below the minimum. A key whose lower prediction
exceeds its upper prediction is kept as a crossed ``KeyFit`` and withheld as ``range_invalid``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Mapping, Sequence

from .config import LightGBMRecipe
from .empirical import KeyFit, base_case_values
from .records import PriceRecord
from .vocabulary import CostKeyTuple, key_text

FEATURES = ("part_code", "operation", "vehicle_class")
EXCLUDED_FEATURES = ("model_year", "side", "damage_type", "workshop_id", "synthetic_date")
MODEL_FILES = {"lower": "model.lower.txt", "upper": "model.upper.txt"}
MODEL_REPORT_FILE = "model.json"
RAW_PLACES = 6  # predictions are fixed to this many places before any Decimal arithmetic


class LightGBMUnavailable(RuntimeError):
    """``lightgbm`` (or its OpenMP runtime) is not usable in this environment."""


def _lightgbm():
    try:
        import lightgbm
    except (ImportError, OSError) as exc:  # OSError: the wheel is present but libomp/libgomp is not
        raise LightGBMUnavailable(
            "method lightgbm_quantile needs lightgbm and its OpenMP runtime (macOS: libomp; Linux images: "
            "libgomp1). The contingency fallback builds with --method empirical_percentile") from exc
    return lightgbm


@dataclass(frozen=True)
class LightGBMFit:
    """Per-key bounds in the shared ``KeyFit`` shape, plus what is needed to record the fit."""

    fits: Mapping[CostKeyTuple, KeyFit]
    crossed_keys: tuple[CostKeyTuple, ...]
    report: Mapping[str, Any]
    model_text: Mapping[str, str]


class _Encoder:
    """Fixed integer codes per feature, taken from the frozen eligible grid (never from the data)."""

    def __init__(self, keys: Sequence[CostKeyTuple]):
        self.levels = tuple(tuple(sorted({key[i] for key in keys})) for i in range(len(FEATURES)))
        self._codes = tuple({level: code for code, level in enumerate(levels)} for levels in self.levels)

    def encode(self, key: CostKeyTuple) -> list[int]:
        try:
            return [self._codes[i][key[i]] for i in range(len(FEATURES))]
        except KeyError as exc:
            raise ValueError(f"{key_text(key)} is outside the frozen eligible grid") from exc


def _rows(records: Sequence[PriceRecord]) -> list[tuple[CostKeyTuple, str, Decimal]]:
    """One (key, base case, median amount) row per independent base case, in a stable order."""
    return [(key, case, amount) for key, cases in sorted(base_case_values(records).items())
            for case, amount in sorted(cases.items())]


def _matrix(rows: Sequence[tuple[CostKeyTuple, str, Decimal]], encoder: _Encoder):
    import numpy as np

    features = np.array([encoder.encode(key) for key, _, _ in rows], dtype=np.int32).reshape(len(rows), len(FEATURES))
    target = np.array([math.log(float(amount)) for _, _, amount in rows], dtype=np.float64)
    return features, target


def _pinball(target, prediction, alpha: float) -> float:
    import numpy as np

    error = target - prediction
    return float(np.mean(np.maximum(alpha * error, (alpha - 1.0) * error)))


def fit_lightgbm(train: Sequence[PriceRecord], validation: Sequence[PriceRecord], *,
                 quantiles: tuple[Decimal, Decimal], recipe: LightGBMRecipe,
                 eligible_keys: Sequence[CostKeyTuple]) -> LightGBMFit:
    """Fit on the train partition only; the validation partition is used for early stopping only.

    Returns one ``KeyFit`` for every key with at least one train base case, carrying the
    same support and record counts ``fit_empirical`` would give.
    """
    lightgbm = _lightgbm()
    encoder = _Encoder(eligible_keys)
    train_rows, validation_rows = _rows(train), _rows(validation)
    if not train_rows:
        raise ValueError("no training base cases")
    features, target = _matrix(train_rows, encoder)
    names = list(FEATURES)

    def dataset(x, y, reference=None):
        return lightgbm.Dataset(x, label=y, feature_name=names, categorical_feature=names, reference=reference,
                                free_raw_data=False)

    train_set = dataset(features, target)
    valid = None
    if validation_rows:
        valid_features, valid_target = _matrix(validation_rows, encoder)
        valid = dataset(valid_features, valid_target, reference=train_set)

    record_counts: dict[CostKeyTuple, int] = defaultdict(int)
    for record in train:
        record_counts[record.key] += 1
    case_counts: dict[CostKeyTuple, int] = defaultdict(int)
    for key, _, _ in train_rows:
        case_counts[key] += 1
    keys = sorted(case_counts)
    key_features = _matrix([(key, "", Decimal(1)) for key in keys], encoder)[0]

    predictions, models, text = {}, {}, {}
    for name, quantile in zip(("lower", "upper"), quantiles):
        alpha = float(quantile)
        params = {"objective": "quantile", "alpha": alpha, "metric": "quantile", **recipe.params()}
        callbacks = [lightgbm.early_stopping(recipe.early_stopping_rounds, verbose=False)] if valid is not None else []
        booster = lightgbm.train(params, train_set, num_boost_round=recipe.n_estimators,
                                 valid_sets=[valid] if valid is not None else None,
                                 valid_names=["validation"] if valid is not None else None, callbacks=callbacks)
        rounds = booster.best_iteration or booster.current_iteration()
        predictions[name] = booster.predict(key_features, num_iteration=rounds)
        text[name] = booster.model_to_string(num_iteration=rounds)
        models[name] = {
            "quantile": format(quantile, "f"), "trees_used": int(rounds), "trees_allowed": recipe.n_estimators,
            "early_stopped": valid is not None and rounds < recipe.n_estimators,
            "train_pinball_loss": round(_pinball(target, booster.predict(features, num_iteration=rounds), alpha), 6),
            "validation_pinball_loss": None if valid is None else round(
                _pinball(valid_target, booster.predict(valid_features, num_iteration=rounds), alpha), 6),
            "model_file": MODEL_FILES[name]}

    fits, crossed = {}, []
    for index, key in enumerate(keys):
        lower = Decimal(f"{math.exp(float(predictions['lower'][index])):.{RAW_PLACES}f}")
        upper = Decimal(f"{math.exp(float(predictions['upper'][index])):.{RAW_PLACES}f}")
        fits[key] = KeyFit(key, lower, upper, case_counts[key], record_counts[key])
        if fits[key].crossed:
            crossed.append(key)
    report = {
        "method": "lightgbm_quantile", "library": "lightgbm", "library_version": lightgbm.__version__,
        "features": list(FEATURES), "feature_levels": {f: list(levels) for f, levels in zip(FEATURES, encoder.levels)},
        "excluded_features": list(EXCLUDED_FEATURES),
        "constant_fields": {"currency": "recorded, not a feature", "cost_basis": "recorded, not a feature"},
        "target": "ln(amount)", "row_unit": "one row per independent base case: the median of its quotes",
        "recipe": recipe.describe(),
        "fit_partition": "train", "early_stopping_partition": "validation" if valid is not None else None,
        "train_rows": len(train_rows), "validation_rows": len(validation_rows), "predicted_keys": len(keys),
        "models": models, "crossed_keys": [key_text(k) for k in crossed],
        "raw_bound_places": RAW_PLACES,
        "serving": "never served as a model; only the published table is read at claim time",
    }
    return LightGBMFit(fits, tuple(crossed), report, text)


__all__ = ["EXCLUDED_FEATURES", "FEATURES", "MODEL_FILES", "MODEL_REPORT_FILE", "LightGBMFit",
           "LightGBMUnavailable", "fit_lightgbm"]
