# M7 plan: LightGBM quantile model for reference cost ranges

Status: **accepted** by the user on 2026-10-02, with every recommendation in section 6 adopted. Owner lane: 4 (costs and checks). Baseline commit: `4edb356` on `main`.

Sources: [M7 specification](../specs/module-07-reference-cost-ranges.md), [training specification section 7.4](../specs/model_training_specification.md), [evaluation plan section 8](../specs/evaluation_plan.md), [proposal v2](../CLAIM-CMEV_project_proposal_v2.md) sections 8.3, 11.6, 13.2 C and 13.4.

This document is a plan. It does not record results. Results belong in `artifacts/evaluation/` and in the [CONTEXT.md](../../CONTEXT.md) handoff once they are measured.

Progress: phases 1 to 6 were carried out on 2026-10-02; see the CONTEXT.md entry "M7 LightGBM quantile method" for what was built and measured. Two points differ from the text below. Model files are flat (`model.lower.txt`, `model.upper.txt`, `model.json`) and not in a `model/` folder, because the table loader refuses nested paths. Decision 5 was settled the same day: the rule selected `lightgbm_quantile` and the user made it the default build method, so `lightgbm` is a core dependency (not the optional extra described in section 4) and the app image installs `libgomp1`.

## 1. Goal and scope

M8 answers "is this price in range?" by looking up a range that M7 publishes. Today M7 publishes ranges from an empirical percentile method with a conformal correction. The learned method the M7 specification calls `lightgbm_quantile` is not built: the build manifest records `learned_comparator: lightgbm_quantile, status: not_run`.

This plan adds that method beside the baseline, compares the two on the same data, selects one on validation, and publishes the table.

In scope:

- the LightGBM quantile model and its tests;
- the method comparison and the selection on validation;
- the reduced-support experiment (research question RQ4);
- scoring the injected anomalies (experiment C).

Out of scope:

- any change to M8 rules or the review UI;
- features beyond part, operation and vehicle class (the v2 cost key is fixed);
- real price data;
- the showcase-key sparsity gap (with seed 20260924 the key `front-bumper / replace / sedan_standard` is withheld for support). That is a separate generator change.

## 2. Inputs

| Input | Source | Size with seed 20260924 |
| --- | --- | --- |
| Synthetic quotes | `prices.csv` from the existing generator | 7,735 quotes, 2,623 independent base cases |
| Train partition | Existing split, grouped by base case | 1,233 base cases, 3,614 quotes |
| Validation partition | Early stopping, method selection, support threshold | 330 base cases, 986 quotes |
| Calibration partition | Conformal offset only | 215 base cases, 613 quotes |
| Test partition | Final report, membership reserved by hash before any fitting | 463 base cases, 1,361 quotes |
| Injected-anomaly run | Existing separate generator run, experiment C only | 3 base cases per key, 10 percent labelled at +25, +50 and -30 percent |
| Recipe | Training specification section 7.4.2 | See section 4 |

Model features: `part_code`, `operation`, `vehicle_class`, all categorical. Target: the price. Currency and cost basis are constant and are recorded, not used as features. Excluded and recorded in the manifest: model year, side, damage type, workshop identifier and record date.

## 3. Outputs

| Output | Location | Contents |
| --- | --- | --- |
| Published range table | `artifacts/cost_tables/<version>/ranges.csv` and `ranges.json` | Same row schema as today, with `method = lightgbm_quantile`. M8 reads it unchanged |
| Model files | `artifacts/cost_tables/<version>/model/` | The two boosters (5 and 95 percent), the recipe, the best iteration counts |
| Build manifest and metrics | Same folder | Method, validation and test coverage, widths, support counts, crossed-bound count, `learned_comparator.status = run` |
| Method comparison report | `artifacts/evaluation/m7-method-comparison/` | Empirical against LightGBM on identical partitions: coverage, width, available-range rate, by support group |
| RQ4 report | `artifacts/evaluation/rq4-reduced-support/` | Both methods at decreasing reference cases per key |
| Experiment C report | `artifacts/evaluation/experiment-c/` | Ordinary-price exceedance, and injected-anomaly precision and recall by magnitude, in separate tables |
| Documentation | M7 specification checklist, `configs/costs/cost_table.yaml`, CONTEXT.md | Only boxes with evidence are ticked |

`artifacts/` is ignored by Git. Reports are regenerated from the recorded seed.

## 4. Design

The existing build passes one small object per key, `KeyFit`, holding the unrounded lower and upper bounds and the support count. The new method produces the same objects, so the support sweep, the conformal step, row assembly, validation, test metrics and publication are reused unchanged.

1. New module `src/claim_cmev/costs/reference/lightgbm_quantile.py`. One function fits on train and returns a `KeyFit` per key.
2. Training rows: one row per independent base case, using that base case's median quote. This matches the baseline, so support means the same thing in both methods.
3. Target: the natural log of the price. The generator's noise is multiplicative and the existing conformal offset is already on the log scale.
4. Two models with quantile loss at 0.05 and 0.95, using the specification recipe: at most 400 trees, learning rate 0.05, 15 leaves, at least 20 rows per leaf, L2 of 1.0, early stopping after 50 rounds on validation pinball loss, fixed seed, deterministic mode, one thread.
5. Prediction: one lower and one upper bound per eligible key, converted to exact `Decimal` and rounded to cents once at publication. Floats exist only inside LightGBM.
6. Safety rules that do not change:
   - support is the count of distinct training base cases for the key;
   - a key below the support threshold, or with no records, is withheld even though the model could predict it;
   - a key whose lower prediction exceeds its upper prediction is withheld and never published.
7. Conformal calibration: the existing log-scale CQR step, refitted for this method on the calibration partition.
8. Configuration: `cost_table.yaml` gains a `lightgbm` recipe block and a new `config_version`. The loader's guard that rejects every method except the empirical one is removed.
9. Dependency: `lightgbm` is an optional extra, imported only when that method is selected. The API and consolidator images are unchanged.

## 5. Work steps

| Phase | Work | Evidence that it is done | Effort (person-days) |
| --- | --- | --- | --- |
| 1 Model | Module, configuration block, method switch in `build.py`, model files written | A `lightgbm_quantile` table builds and the existing M8 tests pass against it | 1.0 to 1.5 |
| 2 Tests | `tests/unit/m7/test_m7_lightgbm.py`: no crossed, negative or non-finite bound is published; same key set and support counts as the baseline; the same seed gives the same table; sparse and empty keys stay withheld; no float reaches a money field; the build refuses an injected-anomaly file | M7 suite passes; the existing M7, M8 and contract tests still pass | 0.5 |
| 3 Comparison and selection | Script that builds both methods on one split, applies the selection rule on validation, then reports test once per method | Comparison report; selected method frozen in `cost_table.yaml` | 0.5 to 1.0 |
| 4 RQ4 | Cap training base cases per key at 16, 12, 8, 5 and 3, with validation, calibration and test fixed. Report both methods at each step. Model ranges for keys below the threshold are reported as "model only, not served" | RQ4 report with counts and denominators | 1.0 |
| 5 Experiment C | Score the injected run against both tables | Report with ordinary exceedance and injected anomalies kept separate | 0.5 |
| 6 Handoff | Specification checkboxes, CONTEXT.md entry | Documentation updated | 0.5 |

Total: about 4 to 5 person-days, in line with item R13 in CONTEXT.md (3 to 5).

## 6. Decisions

All six were accepted as recommended on 2026-10-02.

| # | Decision | Accepted choice |
| --- | --- | --- |
| 1 | Training rows | One row per independent base case (its median quote), not one row per quote |
| 2 | Target scale | Log price |
| 3 | Method selection rule, fixed before any result is seen | Among methods whose validation coverage is inside 0.85 to 0.95, select the one with the narrower median range. On a tie, or if neither qualifies, keep `empirical_percentile` |
| 4 | Reason code for a crossed-bound key | Reuse `range_invalid`, which M8 already understands, and record the crossed count in the manifest. The training specification's `crossed_bounds` wording is not adopted as a separate code |
| 5 | Whether `cmev-cost-bootstrap` builds with LightGBM | Decided after phase 3, on 2026-10-02: yes. The default method is `lightgbm_quantile` |
| 6 | Extent | Full plan, phases 1 to 6 |

## 7. Risks and limits

- The result may be unremarkable. With three categorical features the model is close to a smoothed lookup table. It may match the baseline or lose to it. Either result is reported; this plan does not assume that LightGBM wins.
- Synthetic data only. Results show that the method and the calibration work under the generator. They say nothing about real Singapore repair prices, and every output stays marked synthetic.
- Reproducibility across machines. LightGBM floats can differ slightly between platforms, which could move a bound by a cent and so change the table's version hash. Same-machine reproducibility is tested; the cross-platform limit is documented, as the M7 specification allows.
- Small leaves. There are about 10 training base cases per key and the recipe requires 20 rows per leaf, so the model pools across keys. That is intended, but it could blur a part whose price differs sharply from its neighbours. The per-key coverage report shows it if it happens.
- Local setup. LightGBM on macOS needs the `libomp` system library. Linux images need `libgomp1`.

## 8. What stays untouched

M8 rules and tests, the lookup interface, the published row schema, the split and the test reservation, and the empirical baseline, which stays buildable as the contingency fallback under proposal v2 section 12.4.
