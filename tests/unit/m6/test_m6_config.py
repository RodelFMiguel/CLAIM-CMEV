"""M6 linking configuration: proposed spec defaults, strict validation and the version label."""
from pathlib import Path

import pytest
from pydantic import ValidationError
import yaml

from claim_cmev.documents.pen_marks import load_linking_config
from claim_cmev.documents.pen_marks.config import CONFIG_ENV, DEFAULT_CONFIG_PATH


def test_default_config_carries_the_spec_proposed_values():
    config = load_linking_config()
    assert DEFAULT_CONFIG_PATH.name == "m6_linking.yaml"
    assert config.link_config_version == "m6-linking/0.1.0"
    assert config.score_threshold("exclusion") == 0.50 and config.score_threshold("price_change") == 0.45
    assert (config.band.extend_above_row_heights, config.band.extend_below_row_heights) == (0.60, 0.25)
    weights = config.link.weights
    assert (weights.v, weights.c, weights.rc, weights.col) == (0.45, 0.25, 0.15, 0.15)
    assert (config.link.min_score, config.link.margin_to_second, config.link.candidate_min_score) == (0.5, 0.2, 0.2)
    assert (config.price_change.margin_to_second, config.price_change.amount_overlap_min,
            config.price_change.amount_overlap_second_max) == (0.35, 0.30, 0.10)
    assert config.column.exclusion_columns == "all" and config.column.price_change_columns == ("amount",)
    assert config.column.right_margin_tolerance_px == 40
    assert config.detection.dedupe_iou == 0.60 and config.row.max_marks_per_row == 2
    assert len(config.sha256) == 64


@pytest.mark.parametrize("overrides", [
    {"link": {"weights": {"v": 0.5}}},  # weights no longer sum to one
    {"link": {"candidate_min_score": 0.6}},  # candidates stricter than linking
    {"price_change": {"amount_overlap_second_max": 0.3}},  # PC-1 cannot tell rows apart
    {"price_change": {"margin_to_second": 0.1}},  # looser than the exclusion margin
    {"column": {"price_change_columns": ["unit_price"]}},  # no unit-price geometry in the row contract
    {"detection": {"score_threshold": {"exclusion": 1.5}}},
    {"row": {"max_marks_per_row": 0}},
])
def test_incoherent_overrides_are_rejected(overrides):
    with pytest.raises(ValidationError):
        load_linking_config().with_overrides(overrides)


def test_unknown_and_missing_keys_are_errors(tmp_path: Path):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    extra = {**data, "surprise": 1}
    (tmp_path / "extra.yaml").write_text(yaml.safe_dump(extra), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_linking_config(tmp_path / "extra.yaml")
    missing = {k: v for k, v in data.items() if k != "band"}
    (tmp_path / "missing.yaml").write_text(yaml.safe_dump(missing), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_linking_config(tmp_path / "missing.yaml")


def test_environment_override_and_content_hash(tmp_path: Path, monkeypatch):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["link_config_version"] = "m6-linking/test"
    data["link"]["min_score"] = 0.55
    path = tmp_path / "m6.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV, str(path))
    loaded = load_linking_config()
    assert loaded.link_config_version == "m6-linking/test" and loaded.link.min_score == 0.55
    monkeypatch.delenv(CONFIG_ENV)
    assert loaded.sha256 != load_linking_config().sha256
