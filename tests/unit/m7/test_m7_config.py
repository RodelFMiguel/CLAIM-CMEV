"""M7 configuration: frozen (proposed) grid, taxonomy agreement, exact settings, thin pipeline entrypoints."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from claim_cmev import taxonomy
from claim_cmev.costs.reference.config import (
    DEFAULT_CONFIG_DIR, ConfigError, check_taxonomy, load_cost_table_config, load_generator_config, load_grid,
)
from claim_cmev.costs.reference.records import read_records

REPO = Path(__file__).resolve().parents[3]


def test_grid_matches_the_shared_taxonomy_and_the_fixed_basis():
    basis = check_taxonomy()
    grid = load_grid()
    catalogue = {p["code"] for p in taxonomy.load_parts().meta["parts"] if p["cost_eligible"]}
    assert set(grid.catalogue_parts) == catalogue and len(catalogue) == 18
    assert grid.operations == tuple(taxonomy.cost_eligible_operations())
    assert grid.vehicle_classes == taxonomy.load_vehicle_classes().codes
    assert basis["cost_basis"] == grid.cost_basis == "single_part_pre_tax_no_discount_v1" and grid.currency == "SGD"
    assert len(grid.keys()) == 216 and len(grid.eligible_keys()) == 128
    required = {"front-bumper", "back-bumper", "headlight", "tail-light", "fender", "front-door", "hood", "mirror",
                "windshield", "quarter-panel"}
    assert required <= {part for part, _ in grid.eligible_pairs}
    assert ("windshield", "paint") not in grid.eligible_pairs and ("headlight", "repair") not in grid.eligible_pairs
    assert grid.taxonomy_version.count("+") == 3


def test_every_eligible_key_has_one_documented_positive_base_price():
    config = load_generator_config()
    assert set(config.base_prices) == set(config.grid.eligible_keys())
    assert all(isinstance(v, Decimal) and v > 0 and v.as_tuple().exponent == -2 for v in config.base_prices.values())
    assert config.base_prices[("front-bumper", "replace", "suv_crossover", "SGD")] == Decimal("960.00")


def test_policy_values_are_proposed_and_frozen_from_validation():
    config = load_cost_table_config()
    assert (config.method, config.min_independent_base_cases, config.conformal) == ("empirical_percentile", 8, "cqr")
    assert config.nominal_coverage == Decimal("0.90") and config.quantiles == (Decimal("0.05"), Decimal("0.95"))
    for name in ("eligible_keys.yaml", "base_prices.yaml", "generator.yaml", "splits.yaml", "cost_table.yaml"):
        assert yaml.safe_load((DEFAULT_CONFIG_DIR / name).read_text())["status"] == "proposed"


def test_a_float_money_setting_is_refused(tmp_path):
    copy = tmp_path / "costs"
    shutil.copytree(DEFAULT_CONFIG_DIR, copy)
    data = yaml.safe_load((copy / "base_prices.yaml").read_text())
    data["pairs"]["hood"]["repair"]["price"] = 320.0
    (copy / "base_prices.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="quoted decimal"):
        load_generator_config(copy)


def test_a_price_for_an_ineligible_pair_is_refused(tmp_path):
    copy = tmp_path / "costs"
    shutil.copytree(DEFAULT_CONFIG_DIR, copy)
    data = yaml.safe_load((copy / "base_prices.yaml").read_text())
    data["pairs"]["windshield"]["paint"] = {"price": "100.00", "note": "implausible"}
    (copy / "base_prices.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="exactly the eligible pairs"):
        load_generator_config(copy)


def test_pipeline_entrypoints_generate_and_build(tmp_path):
    run = lambda *args: subprocess.run([sys.executable, *args], cwd=REPO, capture_output=True, text=True, check=True)
    out = run("pipelines/costs/generate_prices.py", "--seed", "5", "--output", str(tmp_path / "prices"))
    assert json.loads(out.stdout)["run_kind"] == "ordinary"
    assert len(read_records(tmp_path / "prices" / "prices.csv")) > 5000
    built = run("pipelines/costs/build_cost_table.py", "--records", str(tmp_path / "prices" / "prices.csv"),
                "--out", str(tmp_path / "registry"))
    assert (tmp_path / "registry" / json.loads(built.stdout)["table_version"] / "build_manifest.json").is_file()
