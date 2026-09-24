"""Generate synthetic M7 price records (training specification section 9.3). Offline only.

    python pipelines/costs/generate_prices.py --seed 20260922 --output data/processed/prices/gen-2026.09.1
    python pipelines/costs/generate_prices.py --run-kind injected_anomaly --output <separate directory>

Ordinary and injected-anomaly records are separate runs with separate manifests; an
injected run is never a reference-build input.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from claim_cmev.costs.reference.config import DEFAULT_CONFIG_DIR, load_generator_config
from claim_cmev.costs.reference.generator import generate_injected_anomalies, generate_prices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True, help="directory for prices.csv and its manifest")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--records", type=int, default=None, help="target record count (default from config)")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--run-kind", choices=("ordinary", "injected_anomaly"), default="ordinary")
    args = parser.parse_args(argv)
    config = load_generator_config(args.config_dir, seed=args.seed)
    if args.records is not None:
        config = replace(config, total_records=args.records)
    generate = generate_prices if args.run_kind == "ordinary" else generate_injected_anomalies
    manifest = generate(config=config, out_dir=args.output)
    print(json.dumps({k: manifest.data[k] for k in ("generator_version", "run_kind", "seed", "record_count",
                                                     "base_case_count")} | {"records": str(manifest.records_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
