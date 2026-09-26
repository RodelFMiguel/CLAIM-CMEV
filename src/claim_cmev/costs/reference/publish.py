"""Write a candidate table, verify it, then promote it atomically into the registry.

A build is written into a staging directory beside the registry, verified with the same
loader the runtime uses, and moved into ``<registry_root>/<table_version>/`` with one
rename. An existing version is never overwritten: an identical rebuild is a no-op and a
different one is refused. The registry pointer changes only on an explicit promotion and
records the prior pointer; existing assessments keep their pinned version regardless.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Iterator, Mapping, Sequence

import yaml

from .eligibility import MEMBER_FIELDS
from .lookup import MANIFEST_FILE, RANGES_FILE, REGISTRY_FILE, load_table_dir, read_registry, table_dir
from .records import file_sha256

RANGE_CSV_FIELDS = (
    "range_id", "part_code", "operation", "vehicle_class", "currency", "cost_basis", "support_status",
    "lower_amount", "upper_amount", "independent_base_case_count", "record_count", "withheld_reason", "method",
    "nominal_coverage", "as_of_date", "cutoff_date", "table_version", "synthetic",
)


class TableExistsError(RuntimeError):
    """A different build already holds this table version; published tables are immutable."""


def write_json(path: Path, value: Any) -> str:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return file_sha256(path)


def write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fields})
    return file_sha256(path)


def write_table_files(directory: Path, *, rows: Sequence[Mapping], members: Sequence[Mapping],
                      exclusions: Sequence[Mapping], documents: Mapping[str, Any],
                      config_snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Write every table file except the manifest; return name -> SHA-256."""
    files = {RANGES_FILE: write_json(directory / RANGES_FILE, list(rows)),
             "ranges.csv": write_csv(directory / "ranges.csv", RANGE_CSV_FIELDS, rows),
             "members.csv": write_csv(directory / "members.csv", MEMBER_FIELDS, members),
             "exclusions.csv": write_csv(directory / "exclusions.csv", MEMBER_FIELDS, exclusions)}
    for name, document in documents.items():
        files[name] = write_json(directory / name, document)
    snapshot_path = directory / "config.snapshot.yaml"
    snapshot_path.write_text(yaml.safe_dump(dict(config_snapshot), sort_keys=True), encoding="utf-8")
    files["config.snapshot.yaml"] = file_sha256(snapshot_path)
    return dict(sorted(files.items()))


@contextmanager
def _registry_lock(registry_root: Path) -> Iterator[None]:
    lock = registry_root / ".registry.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"registry is locked by another publication ({lock}); inspect before retrying") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _register(registry_root: Path, entry: Mapping[str, Any], activate: bool) -> dict[str, Any]:
    registry = read_registry(registry_root)
    registry.setdefault("tables", [])
    registry.setdefault("history", [])
    if not any(t["table_version"] == entry["table_version"] for t in registry["tables"]):
        registry["tables"].append(dict(entry))
    now = datetime.now(timezone.utc).isoformat()
    if activate and registry.get("active_version") != entry["table_version"]:
        registry["history"].append({"at": now, "action": "promote", "from": registry.get("active_version"),
                                    "to": entry["table_version"]})
        registry["active_version"] = entry["table_version"]
    registry.setdefault("active_version", None)
    registry["note"] = ("The active version is the default for new assessments only; every assessment keeps "
                        "the table version it pinned. Tables are synthetic.")
    temporary = registry_root / f".{REGISTRY_FILE}.tmp"
    write_json(temporary, registry)
    os.replace(temporary, registry_root / REGISTRY_FILE)
    return registry


def _freeze(directory: Path) -> None:
    """Make published files read-only; the directory stays removable by its owner."""
    for path in directory.iterdir():
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def promote(staging: Path, registry_root: Path, *, table_version: str, content_hash: str,
            entry: Mapping[str, Any], activate: bool = False) -> tuple[Path, bool]:
    """Atomically move a verified staging build into place. Returns (path, reused_existing)."""
    registry_root = Path(registry_root)
    target = table_dir(registry_root, table_version)
    load_table_dir(staging, table_version)  # the runtime loader must accept the candidate first
    with _registry_lock(registry_root):
        reused = target.exists()
        if reused:
            existing = json.loads((target / MANIFEST_FILE).read_text(encoding="utf-8"))
            shutil.rmtree(staging, ignore_errors=True)
            if existing.get("content_hash") != content_hash:
                raise TableExistsError(f"table {table_version} already exists with different content; "
                                       "a correction needs a new table_version")
        else:
            os.rename(staging, target)
            _freeze(target)
        _register(registry_root, entry, activate)
    return target, reused
