"""Build and publish one M7 reference table from generated records. Offline only.

    python pipelines/costs/build_cost_table.py --records <run>/prices.csv --out artifacts/cost_tables [--promote]

For the whole generate-and-build demo use ``python -m claim_cmev.costs.reference.build``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from claim_cmev.costs.reference.build import build_cost_table
from claim_cmev.costs.reference.config import DEFAULT_CONFIG_DIR, load_cost_table_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=Path, required=True, help="prices.csv beside its generator manifest")
    parser.add_argument("--out", type=Path, required=True, help="registry root, e.g. artifacts/cost_tables")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--table-version", default=None)
    parser.add_argument("--promote", action="store_true", help="make it the default for new assessments")
    args = parser.parse_args(argv)
    manifest = build_cost_table(args.records, config=load_cost_table_config(args.config_dir), out_dir=args.out,
                                table_version=args.table_version, promote_active=args.promote)
    print(json.dumps({k: manifest[k] for k in ("table_version", "published_path", "reused_existing", "key_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
