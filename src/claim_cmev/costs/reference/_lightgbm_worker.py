"""Private offline fitting process. JSON on stdin/stdout; diagnostics on stderr.

Do not import vision libraries here: this interpreter owns LightGBM's native runtime.
No table is published by this worker; only the parent build can validate and publish it.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import sys

from .config import LightGBMRecipe
from .lightgbm_quantile import _fit_lightgbm
from .records import PriceRecord


def main() -> int:
    try:
        payload = json.load(sys.stdin)

        def records(rows):
            return [PriceRecord(**{**row, "amount": Decimal(row["amount"]),
                                   "synthetic_date": date.fromisoformat(row["synthetic_date"])}) for row in rows]

        with redirect_stdout(sys.stderr):
            result = _fit_lightgbm(records(payload["train"]), records(payload["validation"]),
                                   quantiles=tuple(Decimal(q) for q in payload["quantiles"]),
                                   recipe=LightGBMRecipe(**payload["recipe"]),
                                   eligible_keys=[tuple(k) for k in payload["eligible_keys"]])
        response = {"fits": [asdict(f) for f in result.fits.values()], "crossed_keys": result.crossed_keys,
                    "report": result.report, "model_text": result.model_text}
    except Exception as exc:
        json.dump({"error": {"type": type(exc).__name__, "message": str(exc)}}, sys.stdout)
        return 1
    json.dump(response, sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
