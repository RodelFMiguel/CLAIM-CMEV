"""Offline M7 build: screen, split, reserve test, fit, sweep, validate, evaluate once, publish.

Command (deterministic for a given seed and configuration)::

    python -m claim_cmev.costs.reference.build --seed 20260924 --out artifacts/cost_tables --promote

Runs offline only, never inside a claim request. Every output is labelled synthetic.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence
from uuid import uuid4

from claim_cmev.contracts.common import SCHEMA_VERSION, ContractError
from claim_cmev.contracts.costs import count_independent_base_cases

from .config import (
    DEFAULT_CONFIG_DIR, CostTableConfig, canonical_hash, load_cost_table_config, load_generator_config,
)
from .eligibility import member_record, synthetic_members
from .empirical import (
    assemble_rows, conformal_selection, coverage_by_key, evaluate_bounds, final_test_metrics, fit_conformal,
    fit_empirical, served_bounds, support_sweep,
)
from .generator import GENERATOR_MANIFEST, generate_injected_anomalies, generate_prices
from .lookup import MANIFEST_FILE
from .publish import promote, write_json, write_table_files
from .records import file_sha256, read_records
from .splits import membership_hash, reserve_test_membership, screen_records, split_records
from .validation import BuildValidationError, validate_ranges
from .vocabulary import COST_KEY_FIELDS, INSUFFICIENT_SUPPORT, SYNTHETIC_NOTICE

REPO_ROOT = Path(__file__).resolve().parents[4]
BUILT_BY = "claim_cmev.costs.reference.build"


def _code_revision() -> dict[str, Any]:
    try:
        run = lambda *args: subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                                           timeout=20, check=True).stdout.strip()
        return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "reason": "git revision unavailable in this environment"}


def _load_generator_manifest(records_path: Path, config: CostTableConfig) -> dict[str, Any]:
    manifest_path = records_path.parent / GENERATOR_MANIFEST
    if not manifest_path.is_file():
        raise BuildValidationError([f"{records_path} has no {GENERATOR_MANIFEST}; lineage is required"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    if manifest.get("run_kind") != "ordinary":
        problems.append("an injected-anomaly run never builds a reference table; keep experiment C separate")
    if manifest.get("synthetic") is not True:
        problems.append("the core build only accepts documented synthetic generator runs")
    if manifest.get("output", {}).get("sha256") != file_sha256(records_path):
        problems.append("the records file does not match its generator manifest hash")
    if manifest.get("cost_basis") != config.grid.cost_basis or manifest.get("currency") != config.grid.currency:
        problems.append("the generator run uses a different basis or currency; bases and currencies never pool")
    if problems:
        raise BuildValidationError(problems)
    return manifest


def _check_membership(rows: Sequence[dict], members: Sequence[dict], exclusions: Sequence[dict]) -> None:
    """Members validate as the shared contract, and published support equals the distinct train
    base cases among them, counted by the contract's own function (never by quote)."""
    try:
        records = [member_record(m) for m in (*members, *exclusions) if m["source_record_id"] and m["base_case_id"]]
    except (ValueError, ContractError) as exc:
        raise BuildValidationError([f"membership row is not a valid ReferenceBuildMember: {exc}"]) from exc
    by_key: dict[tuple, list] = {}
    for record in records:
        if record.included and record.split == "train" and record.cost_key is not None:
            key = record.cost_key
            by_key.setdefault((key.part_code, key.operation, key.vehicle_class, key.currency), []).append(record)
    problems = []
    for row in rows:
        key = (row["part_code"], row["operation"], row["vehicle_class"], row["currency"])
        counted = count_independent_base_cases(by_key.get(key, ()), split="train")
        if counted != row["independent_base_case_count"]:
            problems.append(f"{key}: row support {row['independent_base_case_count']} != {counted} distinct "
                            "train base cases in membership")
    if problems:
        raise BuildValidationError(problems)


def build_cost_table(records_path: Path, *, config: CostTableConfig, out_dir: Path, table_version: str | None = None,
                     promote_active: bool = False, built_by: str = BUILT_BY) -> dict[str, Any]:
    """Build, validate and publish one immutable table under ``out_dir/<table_version>/``.

    ``out_dir`` is the registry root. Raises ``BuildValidationError`` before anything is
    published when an interval, a basis, a currency or a support count is invalid.
    """
    records_path, registry_root = Path(records_path), Path(out_dir)
    generator = _load_generator_manifest(records_path, config)
    grid, split_config = config.grid, config.split
    included, excluded = screen_records(read_records(records_path), grid=grid, cutoff_date=split_config.cutoff_date)
    split = split_records(included, split_config)
    registry_root.mkdir(parents=True, exist_ok=True)
    staging = registry_root / f".staging-{uuid4().hex}"
    staging.mkdir()
    try:
        # 1. Reserve and hash the final-test membership before any fitting.
        reservation = reserve_test_membership(split)
        write_json(staging / "test_reservation.json", reservation)
        # 2. Fit on train only; select the support threshold on validation only.
        fits = fit_empirical(split.partitions["train"], quantiles=config.quantiles)
        sweep = support_sweep(fits, split.partitions["validation"], config=config,
                              eligible_key_count=len(grid.eligible_keys()))
        frozen = config.min_independent_base_cases
        min_support = frozen if frozen is not None else sweep["selected"]
        if min_support is None:
            raise BuildValidationError(["no support threshold is frozen and the validation sweep selected none"])
        # 3. Conformal offset fitted on the calibration partition only; selected on validation, then frozen.
        conformal_fit = fit_conformal(fits, split.partitions["calibration"], min_support=min_support,
                                      nominal=config.nominal_coverage)
        selection = conformal_selection(fits, split.partitions["validation"], conformal_fit,
                                        min_support=min_support, config=config)
        conformal = config.conformal if config.conformal is not None else selection["selected"]
        if conformal == "cqr" and conformal_fit["offset"] is None:
            raise BuildValidationError(["cqr is configured but the calibration partition gives no finite offset"])
        offset = Decimal(conformal_fit["offset"]) if conformal == "cqr" else None
        policy = {"min_support": min_support, "sweep": sweep, "conformal": conformal, "offset": offset,
                  "conformal_fit": conformal_fit, "conformal_selection": selection}
        as_of = date.fromisoformat(generator["settings"]["dates"]["as_of_date"])
        provenance = {"source_kind": "synthetic", "generator_version": generator["generator_version"],
                      "seed": generator["seed"], "generator_ref": "generator.json"}
        rows = assemble_rows(fits, grid, keys_with_records={r.key for r in included}, min_support=min_support,
                             offset=offset, table_version="", config=config, as_of_date=as_of,
                             provenance=provenance, validation=split.partitions["validation"])
        validate_ranges(rows, min_support=min_support, cost_basis=grid.cost_basis, currency=grid.currency,
                        zero_width_allowed=config.zero_width_allowed, train_records=split.partitions["train"])
        # 4. Policy frozen: touch the reserved test partition exactly once.
        test = split.partitions["test"]
        if membership_hash(test) != reservation["membership_sha256"]:
            raise BuildValidationError(["test membership changed after it was reserved"])
        metrics = final_test_metrics(rows, test, config=config, reservation=reservation)
        validation = evaluate_bounds(served_bounds(rows), split.partitions["validation"], config.support_group_edges)
        members, exclusion_rows = synthetic_members(split.partitions, excluded, cutoff_date=split_config.cutoff_date)
        snapshot = dict(config.snapshot)
        content_hash = canonical_hash({"rows": rows, "members": members, "exclusions": exclusion_rows,
                                       "generator": generator, "config": snapshot, "policy": policy,
                                       "reservation": reservation, "metrics": metrics})
        version = table_version or f"ct-{as_of:%Y%m%d}-{content_hash[:8]}"
        build_id = f"build-{content_hash[:12]}"
        for row in rows:
            row["table_version"] = version
        for member in (*members, *exclusion_rows):
            member.update(build_id=build_id, table_version=version)
        _check_membership(rows, members, exclusion_rows)
        metrics = {"table_version": version, "notice": SYNTHETIC_NOTICE,
                   "validation": {"partition": "validation", "min_independent_support": min_support,
                                  "conformal": conformal, **validation,
                                  "by_key": coverage_by_key(served_bounds(rows), split.partitions["validation"])},
                   "final_test": metrics, "support_sweep_ref": "support_sweep.json",
                   "calibration_ref": "calibration.json",
                   "learned_comparator": {"method": "lightgbm_quantile", "status": config.lightgbm_status,
                                          "note": "not run; RQ4 conclusions are limited to the empirical method"}}
        splits_doc = {"split": split_config.describe(), "split_config_hash": split.config_hash,
                      "partitions": split.summary(), "support_after_split": split.support_after_split()}
        calibration_doc = {"synthetic": True, "table_version": version, "fit": conformal_fit,
                           "selection": selection, "applied": conformal}
        documents = {"metrics.json": metrics, "support_sweep.json": sweep | {"table_version": version},
                     "calibration.json": calibration_doc, "generator.json": generator, "splits.json": splits_doc}
        files = write_table_files(staging, rows=rows, members=members, exclusions=exclusion_rows,
                                  documents=documents, config_snapshot=snapshot)
        files["test_reservation.json"] = file_sha256(staging / "test_reservation.json")
        manifest = _manifest(config=config, version=version, build_id=build_id, content_hash=content_hash,
                             generator=generator, split=split, reservation=reservation, policy=policy, rows=rows,
                             exclusion_rows=exclusion_rows, validation=validation, metrics=metrics, as_of=as_of,
                             files=dict(sorted(files.items())), built_by=built_by)
        write_json(staging / MANIFEST_FILE, manifest)
        entry = {"table_version": version, "build_id": build_id, "content_hash": content_hash,
                 "manifest_sha256": file_sha256(staging / MANIFEST_FILE), "built_at": manifest["built_at"],
                 "cutoff_date": manifest["cutoff_date"], "method": manifest["method"],
                 "calibration_method": None if offset is None else conformal_fit["method"],
                 "release_status": "candidate", "synthetic": True}
        path, reused = promote(staging, registry_root, table_version=version, content_hash=content_hash,
                               entry=entry, activate=promote_active)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if reused:
        manifest = json.loads((path / MANIFEST_FILE).read_text(encoding="utf-8"))
    return manifest | {"published_path": str(path), "reused_existing": reused}


def _manifest(*, config: CostTableConfig, version: str, build_id: str, content_hash: str, generator: dict, split,
              reservation: dict, policy: dict, rows: Sequence[dict], exclusion_rows: Sequence[dict],
              validation: dict, metrics: dict, as_of: date, files: dict, built_by: str) -> dict[str, Any]:
    """Build manifest: integration contracts 9.3 fields plus the M7 manifest fields."""
    sweep, min_support, fit = policy["sweep"], policy["min_support"], policy["conformal_fit"]
    applied = policy["offset"] is not None
    calibration = None if not applied else {
        "method": fit["method"], "applied": True, "offset": fit["offset"], "alpha": fit["alpha"],
        "scores": fit["scores"], "group_definition": fit["group_definition"],
        "partition_ref": {"ref": "splits.json#partitions.calibration", "sha256": split.hashes["calibration"]},
        "report_ref": "calibration.json"}
    withheld: dict[str, int] = {}
    for row in rows:
        if row["withheld_reason"]:
            withheld[row["withheld_reason"]] = withheld.get(row["withheld_reason"], 0) + 1
    excluded: dict[str, int] = {}
    for row in exclusion_rows:
        excluded[row["inclusion_reason"]] = excluded.get(row["inclusion_reason"], 0) + 1
    supported = sum(1 for r in rows if r["support_status"] == "supported")
    ref = lambda p: {"ref": f"splits.json#partitions.{p}", "sha256": split.hashes[p]}
    zero_width = sum(1 for r in rows if r["support_status"] == "supported" and r["lower_amount"] == r["upper_amount"])
    return {
        "schema_version": SCHEMA_VERSION, "table_version": version, "build_id": build_id, "status": "published",
        "release_status": "candidate", "synthetic": True, "notice": SYNTHETIC_NOTICE,
        "method": config.method, "nominal_coverage": float(config.nominal_coverage),
        "nominal_coverage_text": format(config.nominal_coverage, "f"),
        "quantiles": [float(q) for q in config.quantiles], "percentile_definition": config.percentile_definition,
        "base_case_aggregation": config.base_case_aggregation,
        "cost_key_fields": list(COST_KEY_FIELDS), "excluded_key_fields": ["model_year", "side", "damage_type"],
        "cost_basis": config.grid.cost_basis, "currency": config.grid.currency,
        "source": {"kind": "synthetic", "generator_ref": "generator.json",
                   "generator_version": generator["generator_version"], "seed": generator["seed"],
                   "records_sha256": generator["output"]["sha256"]},
        "generator_version": generator["generator_version"], "generator_seed": generator["seed"],
        "splits": {"train_ref": ref("train"), "validation_ref": ref("validation"),
                   "calibration_ref": ref("calibration"),
                   "test_ref": {"ref": "test_reservation.json", "sha256": reservation["membership_sha256"]}},
        "partition_hashes": dict(split.hashes), "split_version": split.config.split_version,
        "split_config_hash": split.config_hash, "cutoff_date": split.config.cutoff_date.isoformat(),
        "as_of_date": as_of.isoformat(),
        "min_independent_support": min_support, "min_independent_base_cases": min_support,
        "support_selection": {"selected_on": "validation",
                              "frozen_in_config": config.min_independent_base_cases is not None,
                              "frozen_at": config.config_version, "sweep_selected": sweep["selected"],
                              "agrees_with_sweep": sweep["selected"] == min_support,
                              "validation_metric": sweep["criterion"], "sweep_ref": "support_sweep.json"},
        "calibration": calibration, "conformal": calibration,
        "conformal_decision": {"applied": policy["conformal"], "selected_on": "validation",
                               "validation_selected": policy["conformal_selection"]["selected"],
                               "frozen_in_config": config.conformal is not None,
                               "note": config.conformal_decision,
                               "calibration_partition_sha256": split.hashes["calibration"]},
        "learned_comparator": {"method": "lightgbm_quantile", "status": config.lightgbm_status},
        "key_count": supported, "supported_key_count": supported, "grid_key_count": len(rows),
        "eligible_key_count": len(config.grid.eligible_keys()),
        "withheld_key_count": withheld.get(INSUFFICIENT_SUPPORT, 0), "withheld_by_reason": withheld,
        "members_ref": "members.csv", "exclusions_ref": "exclusions.csv", "metrics_ref": "metrics.json",
        "exclusions": [{"reason_code": k, "record_count": v} for k, v in sorted(excluded.items())],
        "validation_results": {"coverage": validation["coverage"], "evaluated_records": validation["evaluated_records"],
                               "mean_width": metrics["final_test"]["width"]["mean_width"],
                               "by_support_group": validation["by_support_group"]},
        "final_test_summary": {k: metrics["final_test"]["coverage"][k]
                               for k in ("coverage", "evaluated_records", "covered")}
                              | {"target_status": metrics["final_test"]["target_status"]},
        "interval_integrity": {"crossed_bounds": 0, "zero_width_allowed": config.zero_width_allowed,
                               "zero_width_keys": zero_width},
        "taxonomy_version": config.grid.taxonomy_version, "taxonomy_versions": dict(config.grid.taxonomy_versions),
        "eligible_keys_version": config.grid.version,
        "eligible_keys_hash": canonical_hash(config.snapshot.get("eligible_keys.yaml")),
        "base_prices_version": generator.get("base_prices_version"), "config_version": config.config_version,
        "content_hash": content_hash, "files": files,
        "built_at": datetime.now(timezone.utc).isoformat(), "built_by": built_by, "code_revision": _code_revision(),
    }


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Generate, split, fit, validate and publish a synthetic M7 table.")
    parser.add_argument("--seed", type=int, default=None, help="generator seed (default: configs/costs/generator.yaml)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "artifacts" / "cost_tables", help="registry root")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--prices-dir", type=Path, default=None,
                        help="generated records location (default: <out>/_generated/prices)")
    parser.add_argument("--table-version", default=None, help="explicit version (default: derived from content)")
    parser.add_argument("--promote", action="store_true", help="make this table the default for new assessments")
    parser.add_argument("--with-injected", action="store_true",
                        help="also write the separate injected-anomaly run (experiment C input, never built)")
    args = parser.parse_args(argv)
    generator_config = load_generator_config(args.config_dir, seed=args.seed)
    prices_root = (args.prices_dir or args.out / "_generated" / "prices") / generator_config.generator_version
    run_dir = prices_root / f"seed-{generator_config.seed}"
    ordinary = generate_prices(config=generator_config, out_dir=run_dir / "ordinary")
    if args.with_injected:
        generate_injected_anomalies(config=generator_config, out_dir=run_dir / "injected_anomaly")
    manifest = build_cost_table(ordinary.records_path, config=load_cost_table_config(args.config_dir),
                                out_dir=args.out, table_version=args.table_version, promote_active=args.promote)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    manifest = run(argv)
    summary = {k: manifest[k] for k in ("table_version", "published_path", "reused_existing", "key_count",
                                        "grid_key_count", "withheld_by_reason", "min_independent_support",
                                        "support_selection", "final_test_summary", "content_hash")}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
