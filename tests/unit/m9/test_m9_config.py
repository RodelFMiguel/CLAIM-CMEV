"""M9 configuration: strict loading, contract-aligned dismissal reasons, proposed defaults."""
from pathlib import Path

from pydantic import ValidationError
import pytest
import yaml

from claim_cmev.contracts.review import DISMISSAL_REASONS
from claim_cmev.review import load_review_config
from claim_cmev.review.config import DEFAULT_CONFIG_PATH


def write(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "m9_review.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_repository_config_loads_with_the_proposed_defaults():
    config = load_review_config()
    assert config.config_version.startswith("m9-review/")
    assert set(config.review.dismissal_reasons) == set(DISMISSAL_REASONS)
    assert config.review.note_required_for_reasons == ("other",)
    assert not config.finalize.enforce_p1_assessment_not_superseded
    assert not config.finalize.enforce_p2_completeness_confirmed, "P1 and P2 await a team decision"
    assert not config.review.explainer_enabled and not config.review.usability_telemetry_enabled
    assert config.amounts.max_decimal_places == 2 and not config.amounts.allow_zero


def test_dismissal_reason_drift_is_refused(tmp_path):
    path = write(tmp_path, lambda d: d["review"]["dismissal_reasons"].append("too_expensive"))
    with pytest.raises(ValidationError):
        load_review_config(path)


def test_unknown_and_missing_keys_are_refused(tmp_path):
    with pytest.raises(ValidationError):
        load_review_config(write(tmp_path, lambda d: d["finalize"].update(enforce_p3=True)))
    with pytest.raises(ValidationError):
        load_review_config(write(tmp_path, lambda d: d["amounts"].pop("max_amount")))


def test_environment_override(tmp_path, monkeypatch):
    path = write(tmp_path, lambda d: d.update(config_version="m9-review/9.9.9"))
    monkeypatch.setenv("CMEV_M9_REVIEW_CONFIG", str(path))
    assert load_review_config().config_version == "m9-review/9.9.9"
