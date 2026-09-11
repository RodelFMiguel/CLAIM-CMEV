# Downloading project datasets

Run from the repository root with Python 3.9 or later (use `python` instead of
`python3` on Windows where appropriate). The script also resolves its catalogue
and default output correctly when launched from a different directory.

```sh
# Inspect every source and its current prerequisites; no writes or network calls.
python3 scripts/download_datasets.py --dry-run --include-reserve

# Acquire all non-reserve sources for which access/input prerequisites are ready.
python3 scripts/download_datasets.py

# Include the reserve dataset and all DocILE synthetic/unlabeled archives.
python3 scripts/download_datasets.py --include-reserve --docile-extras

# Or select a bounded subset.
python3 scripts/download_datasets.py --datasets cord docile
```

Default selection includes primary, supplementary and local project inputs.
Carparts-Seg is reserve-only unless selected explicitly or with
`--include-reserve`. DocILE defaults to annotated train/validation and test.
`--docile-extras` adds synthetic, unlabeled metadata and 94 unlabeled chunks;
this is a large acquisition, not needed for an initial document baseline.

The downloader continues past missing prerequisites and failures, writes an
aggregate report, and returns **1 if any selected source is incomplete**. Exit 0
means all selected acquisitions completed or existing acquisitions passed their
hash checks. A dry run returns 0 for a valid plan even when access is missing.
CLI usage errors return 2; interruption returns 130.

## Access and source catalogue

The maintained catalogue is [dataset_sources.json](../data/manifests/dataset_sources.json).
Source pages and public metadata were inspected on **2026-09-11**. Access and
licence notes describe available evidence, not a legal determination. The source
rows cover proposal section 12; freMTPL2 and PASCAL-Part are deliberately excluded
by the proposal.

| ID | Download mechanism and prerequisite |
| --- | --- |
| `hitl` | [Publisher form](https://humansintheloop.org/resources/datasets/car-parts-and-car-damages-dataset/), then locally configured authorized URLs/files for both parts and damage. Publisher states CC0. |
| `dsmlr` | [Publisher repository](https://github.com/dsmlr/Car-Parts-Segmentation), pinned commit ZIP. An explicit dataset licence was not found in its README; record owner permission evidence before acquisition. |
| `vehide` | [Proposed Kaggle source](https://www.kaggle.com/datasets/hendrichscullen/vehide-dataset-automatic-vehicle-damage-detection), through the official Kaggle CLI. Record original permission evidence; the mirror's Apache-2.0 label alone does not resolve the proposal's original-terms question. |
| `cardd` | [Publisher licensing process](https://cardd-ustc.github.io/), then supplied authorized archives/URLs and permission evidence. |
| `crashcar101` | [Author Hugging Face repository](https://huggingface.co/datasets/JensParslov/CrashCar), pinned snapshot. Request access and meet the publisher's non-commercial conditions, then set `HF_TOKEN` for that approved account. |
| `docile` | Get `DOCILE_TOKEN` through the [publisher](https://docile.rossum.ai/). Direct archive names follow the [official downloader](https://github.com/rossumai/docile/blob/main/download_dataset.sh). A token grants download access; retain the accompanying dataset terms. |
| `cord` | Public pinned `naver-clova-ix/cord-v2` snapshot, linked by the [publisher](https://github.com/clovaai/cord). This corrected v2 release contains train/validation/test Parquet files with embedded images/annotations. Publisher states CC BY 4.0, differing from the proposal's CC BY-SA 4.0. |
| `sroie` | Obtain train/test assets through the [official competition portal](https://rrc.cvc.uab.es/?ch=13&com=downloads), then configure files and permission evidence. The portal could not be retrieved during source verification; no alternative mirror is silently substituted. |
| `funsd` | [Publisher ZIP](https://guillaumejaume.github.io/FUNSD/download/). Its page asks users to read the licence; record original terms/access evidence first. |
| `carparts-seg` | ZIP linked by [Ultralytics](https://docs.ultralytics.com/datasets/segment/carparts-seg/). Reserve source; record review of publisher/original-source terms for intended use. |
| `prices` | Supply the project's actual synthetic `prices_dataset.csv`. No public download or verified 630-row file is supplied. |
| `survey-reports` | Supply project-generated synthetic survey reports and their source annotations. |
| `vehicle-groups` | Supply authorized real grouped evaluation photos and permission evidence. No suitable public claim-grouped source is established by the proposal. |

Manual-source entries support multiple archives, images and annotations. Include
**all** publisher-supplied components; acquisition completion only establishes
that the configured files arrived. Sample counts, label coverage and semantic
dataset completeness still require inspection.

## Provider clients and credentials

Direct HTTPS downloads and local imports use the Python standard library.
For Hugging Face and Kaggle sources, install the corresponding official clients
in your chosen environment:

```sh
python3 -m pip install huggingface_hub kaggle
```

The script never installs packages automatically. Kaggle uses its normal
[authentication configuration](https://github.com/Kaggle/kaggle-cli);
authenticate the CLI on your workstation before selecting VehiDE. Credentials
are inherited through the environment/provider configuration, not command-line
arguments. Set `HF_TOKEN` and `DOCILE_TOKEN` through your local secret manager
or shell environment; the script does not load `.env` files automatically.

Hugging Face snapshots are pinned to inspected commit IDs. Dataset raw files and
provider cache metadata stay under the chosen output root. Hugging Face manages
its own download concurrency and retries. Kaggle's download/skip behavior is
delegated to the installed CLI; byte-range resumption is implemented by this
script only for direct HTTPS sources.

## Local source configuration

Copy [the example](../data/manifests/dataset_sources.local.example.json) to
`runtime/dataset-sources.local.json`, which is ignored by Git. Edit it to contain
only your actual supplied sources and permission evidence. You can instead pass
`--sources /path/to/private-sources.json`.

Each key is a catalogue ID. A `permission_evidence` string records where you
verified permission (for example, a private licence file or owner approval
reference). It is required only for catalogue entries with unresolved original
terms or explicit licensing requirements. This records an existing entitlement;
it does not submit forms, accept terms, request access, or obtain permission.

`files` overrides a dataset's built-in provider. Each file needs an output
`name` and exactly one of:

- `url`: an authorized **direct HTTPS file URL**.
- `path`: a local file to copy while preserving the original.

An optional `sha256` checks the file against a publisher or independently
established digest. Without it, the script records a first-acquisition hash for
future local integrity checks; this does not independently authenticate the
publisher's bytes. Relative local paths resolve against the invoking shell's
working directory, so absolute paths are preferable. Use paths native to the
Python interpreter: Linux paths under WSL, Windows paths under Windows Python.

Browser landing pages, Google Drive sharing pages and login pages are not direct
file URLs. For these, use the publisher's authorized browser download and
configure the resulting local archive. The downloader rejects HTML/XML responses
and malformed ZIP files.

Do not commit signed download URLs, tokens, personal filenames or permission
documents. The example contains no credentials. Actual acquisition reports stay
in ignored storage. For collaboration, prepare a reviewed, non-identifying
manifest from those records separately.

## Stored output and repeat runs

Default output:

```text
data/raw/
  <dataset-id>/
    <original archives or files>
    <file>.receipt.json
    acquisition.json
    files/                   # Hugging Face or Kaggle provider output
  download-report-<UTC timestamp>.json
```

Custom locations are supported with `--output`; ensure they are outside tracked
source or separately ignored. Archives are preserved and **not extracted**.
Original split membership is untouched. Synthetic imported inputs retain
synthetic provenance even though their original acquisition files are in raw
storage.

Direct downloads stream through `.part` files, retry transient failures up to
`--retries` (default 3), and use `--timeout` (default 60 seconds per HTTP
operation). Resume requires matching source identity and an HTTP validator
(ETag or Last-Modified). If a server ignores ranges or provides no usable
validator, transfer restarts safely. ZIP CRCs and optional expected SHA-256
checks are evaluated before a file is marked complete.

Completed datasets are verified locally on rerun. Changed configuration,
corrupted completed files, or changed source locators are reported rather than
overwriting an earlier acquisition; use a new output root for a different
release. Unversioned publisher endpoints and Kaggle acquisitions are identified
by acquisition time and file hashes; upstream availability of those exact bytes
is not guaranteed.

A per-dataset lock rejects concurrent writes. After a process is forcibly killed,
verify no downloader is still running before removing its stale
`.download.lock`. If interrupted between publishing a file and writing its
receipt, preserve that file and import it as a local source in a new output root.

The report distinguishes acquired, verified-existing, missing prerequisite and
failed states. Manifests retain source page, revision where available, provenance,
hashes, byte counts and downloader/catalogue hashes. They deliberately leave
usable sample counts unknown and annotation/split validation incomplete.

## Validation

```sh
python3 -m unittest discover -s tests/unit -p test_download_datasets.py -v
```

Tests use tiny synthetic archives and mocked HTTP/provider responses. They
exercise interrupted/resumed transfer, corrupt archives, checksums, repeated
runs, locks, access gates, safe paths, credential redaction and selection.
A small live HTTPS smoke check fetched CORD's public 27-byte README. Full public
archives, gated downloads and installed provider-client integrations were not
executed while preparing this script. No dataset conversion, grouped split
construction, training or accuracy evaluation is claimed.
