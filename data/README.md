# Data workspace

| Directory | Purpose | Git policy |
| --- | --- | --- |
| `raw/` | Immutable source downloads and original imports | Contents ignored |
| `interim/` | Converted/normalised intermediate material | Contents ignored |
| `processed/` | Model-ready examples and labels | Contents ignored |
| `synthetic/` | Generated survey reports, price data and controlled scenes | Contents ignored; tiny safe contract examples go in tests |
| `manifests/` | Dataset provenance, licence/access evidence references, checksums and mapping reports | Track safe metadata only |
| `splits/` | Reproducible group-aware train/validation/calibration/test membership | Track opaque non-identifying IDs and hashes only |

No dataset or `prices_dataset.csv` is provided by this scaffold. Dataset sizes/licences recorded in the proposal need owner verification before use.

A manifest should record source/release, original licence URL/evidence and permitted use, acquisition date, checksum, local locator, usable counts, annotation vocabulary, mapping version, vehicle/template/base-case groups, real/synthetic provenance and exclusions. Avoid credentials, claim identifiers, personal filenames or source documents in committed metadata.

Split related vehicles/views, report templates and correlated synthetic price cases together before training. Record grouping limitations and temporal cutoffs. Preserve declared, agreed and final-approved cost semantics through every conversion. See [technical data lifecycle](../docs/specs/technical_specification.md) and [M7](../docs/specs/module-07-reference-cost-ranges.md).

Use the [dataset acquisition guide](../docs/dataset-downloads.md) and [source catalogue](manifests/dataset_sources.json) to download or import original inputs. Acquisition is separate from annotation validation, conversion and grouped splitting.
