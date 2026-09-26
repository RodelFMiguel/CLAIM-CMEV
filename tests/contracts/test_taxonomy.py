"""Versioned vocabulary: YAML matches the contract literals, no taxonomy merge, HITL by class titles."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from claim_cmev.contracts import ContractError
from claim_cmev.contracts.common import (
    COST_BASIS,
    COST_OPERATIONS,
    COST_VEHICLE_CLASSES,
    DAMAGE_CODES,
    OPERATIONS,
    PART_CODES,
    SIDES,
)
from claim_cmev.contracts.costs import COST_KEY_FIELDS
from claim_cmev.taxonomy import (
    DamageLabel,
    TaxonomyMergeError,
    cost_eligible_operations,
    damage_label,
    load_cost_basis,
    load_damage_cardd,
    load_damage_hitl,
    load_mapping_status,
    load_operations,
    load_parts,
    load_sides,
    load_vehicle_classes,
    merge_damage_vocabularies,
)
from claim_cmev.taxonomy.hitl import classify_supervisely_meta

REPO = Path(__file__).resolve().parents[2]


def test_yaml_vocabulary_matches_contract_literals():
    assert load_parts().codes == PART_CODES and len(PART_CODES) == 21
    assert load_sides().codes == SIDES
    assert load_damage_cardd().codes == DAMAGE_CODES and len(DAMAGE_CODES) == 6
    assert load_operations().codes == OPERATIONS
    assert cost_eligible_operations() == COST_OPERATIONS == ("repair", "replace", "paint")
    assert load_vehicle_classes().codes == COST_VEHICLE_CLASSES
    assert load_mapping_status().codes == ("resolved", "ambiguous", "unmapped")


def test_versions_are_recorded_and_proposed_values_marked():
    assert load_parts().version == "parts-1.0.0"
    assert load_damage_cardd().version.startswith("damage-cardd-")
    assert load_damage_hitl().version.startswith("damage-hitl-")
    assert load_vehicle_classes().status == "proposed"
    assert load_vehicle_classes().meta["unknown"] == "unknown"
    cost_eligible = [p["code"] for p in load_parts().meta["parts"] if p["cost_eligible"]]
    assert len(cost_eligible) == 18
    assert {"front-wheel", "back-wheel", "licence-plate"}.isdisjoint(cost_eligible)


def test_fixed_cost_basis_semantics():
    basis = load_cost_basis()
    assert basis["cost_basis"] == COST_BASIS and basis["currency"] == "SGD"
    assert basis["quantity"] == "1" and basis["tax"] == "excluded" and basis["discounts"] == "excluded"
    assert set(basis["operations"]) == set(COST_OPERATIONS)
    assert tuple(basis["cost_key_fields"]) == COST_KEY_FIELDS
    assert {"side", "damage_type", "model_year"} == set(basis["excluded_from_key"])


def test_damage_taxonomies_are_never_merged():
    cardd, hitl = load_damage_cardd(), load_damage_hitl()
    assert cardd.version != hitl.version
    assert load_damage_hitl().meta["active"] is False
    # dent and scratch share a spelling but are different labels
    for shared in ("dent", "scratch"):
        assert damage_label(shared, cardd) != damage_label(shared, hitl)
    assert damage_label("dent", cardd) == DamageLabel(cardd.version, "dent")
    with pytest.raises(TaxonomyMergeError) as err:
        merge_damage_vocabularies(cardd, hitl)
    assert err.value.reason_code == "taxonomy_merge_refused"
    assert merge_damage_vocabularies(cardd, load_damage_cardd()) is cardd
    for v1_label in ("corrosion", "flaking", "cracked", "missing-part"):
        with pytest.raises(ContractError) as err:
            cardd.require(v1_label)
        assert err.value.reason_code == "taxonomy_version_mismatch"


def _meta(titles):
    return {"classes": [{"title": t, "shape": "polygon", "color": "#000000"} for t in titles], "tags": []}


def test_hitl_subset_is_classified_by_class_titles_not_folder(tmp_path):
    parts_titles = [p["hitl_title"] for p in load_parts().meta["parts"]]
    damage_titles = [d["hitl_title"] for d in load_damage_hitl().meta["codes"]]
    # folder names deliberately say the opposite of the contents, as on disk
    swapped_parts = tmp_path / "Car damages dataset"
    swapped_damage = tmp_path / "Car parts dataset"
    for folder, titles in ((swapped_parts, parts_titles[::-1]), (swapped_damage, damage_titles)):
        folder.mkdir()
        (folder / "meta.json").write_text(json.dumps(_meta(titles)))
    parts = classify_supervisely_meta(swapped_parts)
    damage = classify_supervisely_meta(swapped_damage / "meta.json")
    assert parts.kind == "parts" and parts.title_to_code["License-plate"] == "licence-plate"
    assert damage.kind == "damage" and damage.taxonomy_version.startswith("damage-hitl-")
    assert damage.title_to_code["Paint chip"] == "paint-chip"
    with pytest.raises(ContractError) as err:
        classify_supervisely_meta(_meta(parts_titles[:20] + ["Dent"]))
    assert err.value.reason_code == "hitl_subset_unrecognised"
    with pytest.raises(ContractError):
        classify_supervisely_meta({"classes": []})


@pytest.mark.parametrize("folder,kind", [("Car damages dataset", "parts"), ("Car parts dataset", "damage")])
def test_real_hitl_folders_when_present(folder, kind):
    meta = REPO / "data" / "raw" / folder / "meta.json"
    if not meta.exists():
        pytest.skip("HITL data is not present in this checkout")
    assert classify_supervisely_meta(meta).kind == kind


def test_acquisition_manifest_records_the_folder_swap():
    catalogue = json.loads((REPO / "data" / "manifests" / "dataset_sources.json").read_text(encoding="utf-8"))
    hitl = next(d for d in catalogue["datasets"] if d["id"] == "hitl")
    mapping = {f["folder"]: f["actual_subset"] for f in hitl["subset_mapping"]["folders"]}
    assert mapping == {"Car damages dataset": "parts", "Car parts dataset": "damage"}
    assert hitl["subset_mapping"]["loader"] == "claim_cmev.taxonomy.hitl.classify_supervisely_meta"
