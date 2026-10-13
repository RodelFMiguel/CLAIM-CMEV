"""A verified local cache of real M4 page-reading output for a page dataset.

The builder runs ``run_page_reading`` (the served M4 adapter) over every page of a dataset
laid out like ``data/raw/synthetic_motor_claims_500_w_annotation`` and keeps what M4
returned: the validated ``DocumentPage`` record, the exact ``page_reading.json`` and
``rectified.png`` artifact bytes. The loader reads that cache back for the M5/M6 training
notebooks. Only the builder needs an OCR engine; importing this module needs neither Paddle
nor OpenCV.

Cache layout (schema ``cmev-m4-cache/1``), every path relative to the cache root::

    manifest.json                         written last; one entry per cached page
    pages/<image_id>/document_page.json   DocumentPage.model_dump(mode="json")
    pages/<image_id>/page_reading.json    exact M4 artifact bytes
    pages/<image_id>/rectified.png        exact M4 artifact bytes (the corrected render)
    pages/<image_id>/render.png           only when it differs from rectified.png
    pages/<image_id>/entry.json           the page's manifest entry, for resuming a build

A page M4 could not read is recorded as failed or unreadable with its reasons; it is never
written as an empty success. A build is resumable: a page whose entry and files verify is
not read again, a failed page is. Nothing here is a model result: a cache built from
synthetic pages says nothing about OCR accuracy on real estimates.

    python -m pipelines.documents.m4_cache build --dataset <dir> --cache <dir> [--workers K]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from claim_cmev.contracts.common import make_job_key
from claim_cmev.contracts.documents import DocumentPage
from claim_cmev.vision.transforms import map_points, orientation_matrix, oriented_size

CACHE_SCHEMA = "cmev-m4-cache/1"
MANIFEST_NAME = "manifest.json"
ENTRY_NAME = "entry.json"
PRODUCER_SERVICE = "m4-cache-builder"
OBJECT_URI_PREFIX = "file://cmev-m4-cache/"
"""Logical prefix of the artifact URIs M4 mints. The cached bytes are found through the
manifest's relative paths, never through these URIs."""
VERIFY_MODES = ("none", "hashes")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_FILE_KEYS = ("document_page", "page_reading", "rectified", "render")
_REPO = Path(__file__).resolve().parents[2]


class M4CacheError(ValueError):
    """The cache or its dataset is missing, inconsistent or of another schema."""


# --------------------------------------------------------------------------- shared helpers

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    """Write to a temporary name in the same folder, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(root: Path, relative: str, what: str) -> Path:
    """A manifest path, refused when it is absolute or leaves the root."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise M4CacheError(f"{what}: {relative!r} is not a path inside {root}")
    return root / candidate


def claim_id_for(dataset_name: str, dataset_claim_id: str) -> str:
    """A deterministic contract ``ClaimId`` for one dataset claim.

    Dataset claim ids such as ``SYN-00001`` are not valid claim ids. This is the first 128
    bits of ``sha256("<dataset name>:<dataset claim id>")`` in Crockford base32: 26
    characters, the shape of a ULID. It identifies the claim inside this dataset only.
    """
    value = int.from_bytes(hashlib.sha256(f"{dataset_name}:{dataset_claim_id}".encode()).digest()[:16], "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


# --------------------------------------------------------------------------- dataset

@dataclass(frozen=True)
class DatasetPage:
    """One row of the dataset's ``manifest.csv`` joined with its ``annotations.jsonl`` record."""

    image_id: str
    dataset_claim_id: str
    split: str
    file_name: str
    source_sha256: str  # manifest ``output_sha256``: the hash of the image file itself
    page_type: str
    page_number: int  # the page's number inside its claim, from the annotations


def read_dataset(dataset_dir: str | Path) -> tuple[list[DatasetPage], dict[str, Any]]:
    """Pages in manifest order, and the hashes of the dataset's own index files."""
    root = Path(dataset_dir)
    manifest_path, annotations_path = root / "manifest.csv", root / "annotations.jsonl"
    for path in (manifest_path, annotations_path):
        if not path.is_file():
            raise M4CacheError(f"dataset file missing: {path}")
    annotations: dict[str, dict[str, Any]] = {}
    with annotations_path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                annotations[record["image_id"]] = record
    pages: list[DatasetPage] = []
    with manifest_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            image_id = row["image_id"]
            if not _ID_PATTERN.fullmatch(image_id):
                raise M4CacheError(f"image id {image_id!r} cannot name a cache folder or a job target")
            if image_id not in annotations:
                raise M4CacheError(f"page {image_id}: no record in {annotations_path.name}")
            annotation = annotations[image_id]
            pages.append(DatasetPage(image_id, row["claim_id"], row["split"], row["file_name"], row["output_sha256"],
                                     str(annotation["page_type"]), int(annotation["page_number"])))
    if len({p.image_id for p in pages}) != len(pages):
        raise M4CacheError(f"{manifest_path} lists an image id more than once")
    provenance = root / "provenance.json"
    info = {"manifest_sha256": _file_sha256(manifest_path), "annotations_sha256": _file_sha256(annotations_path),
            "provenance_sha256": _file_sha256(provenance) if provenance.is_file() else None,
            "page_count": len(pages)}
    return pages, info


# --------------------------------------------------------------------------- builder

def m4_source_sha256() -> str:
    """One hash over the M4 source files that decide a page's output.

    The version labels do not change when the working tree does, so a resumed build also
    compares this: pages read by other code are read again, not mixed in.
    """
    import claim_cmev

    package = Path(claim_cmev.__file__).resolve().parent
    files = sorted((package / "documents" / "text_layout").glob("*.py"))
    files += [package / "vision" / "transforms.py", package / "contracts" / "common.py",
              package / "contracts" / "documents.py"]
    digest = hashlib.sha256()
    for path in files:
        digest.update(f"{path.relative_to(package).as_posix()}\n".encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class _BuildContext:
    dataset_dir: Path
    cache_dir: Path
    dataset_name: str
    config: Any  # PageReadingConfig
    versions: Mapping[str, str]
    source_kind: str
    runtime_profile: str
    fingerprint: str


def _fingerprint(dataset_name: str, config: Any, versions: Mapping[str, str], source_kind: str,
                 runtime_profile: str) -> str:
    return _sha256(json.dumps({
        "schema": CACHE_SCHEMA, "dataset_name": dataset_name, "request_versions": dict(versions),
        "page_config_sha256": config.sha256, "m4_source_sha256": m4_source_sha256(),
        "source_kind": source_kind, "runtime_profile": runtime_profile, "producer_service": PRODUCER_SERVICE,
        "object_uri_prefix": OBJECT_URI_PREFIX}, sort_keys=True, separators=(",", ":")).encode())


def _page_dir(cache_dir: Path, image_id: str) -> Path:
    return cache_dir / "pages" / image_id


def _files_verify(cache_dir: Path, entry: Mapping[str, Any]) -> bool:
    for key in _FILE_KEYS:
        relative = entry.get(key)
        if relative is None:
            continue
        path = cache_dir / relative
        if not path.is_file() or _file_sha256(path) != entry.get(f"{key}_sha256"):
            return False
    return True


def _stored_entry(ctx: _BuildContext, page: DatasetPage) -> tuple[dict[str, Any] | None, bool]:
    """The page's stored record when it belongs to this build and verifies; ``(None, stale)`` otherwise."""
    path = _page_dir(ctx.cache_dir, page.image_id) / ENTRY_NAME
    if not path.is_file():
        return None, False
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        entry = stored["entry"]
        same = (stored.get("schema") == CACHE_SCHEMA and stored.get("fingerprint") == ctx.fingerprint
                and entry["image_id"] == page.image_id and entry["source_sha256"] == page.source_sha256
                and entry["source_file"] == page.file_name and entry["dataset_claim_id"] == page.dataset_claim_id
                and entry["split"] == page.split and entry["page_type"] == page.page_type
                and entry["page_number"] == page.page_number)
    except (OSError, ValueError, KeyError, TypeError):
        return None, True
    if not same or not _files_verify(ctx.cache_dir, entry):
        return None, True
    return stored, False


def _read_page(ctx: _BuildContext, engine: Any, page: DatasetPage) -> dict[str, Any]:
    """Run M4 on one dataset page and persist what it returned; returns the stored record."""
    from claim_cmev.documents.text_layout.adapter import PageFileInput, PageReadRequest, run_page_reading

    claim_id = claim_id_for(ctx.dataset_name, page.dataset_claim_id)
    file_id = page.image_id
    job_key = make_job_key(claim_id, 1, "page_read", ctx.versions, target=file_id)
    entry: dict[str, Any] = {
        "image_id": page.image_id, "dataset_claim_id": page.dataset_claim_id, "claim_id": claim_id,
        "split": page.split, "page_type": page.page_type, "page_number": page.page_number,
        "source_file": page.file_name, "source_sha256": page.source_sha256,
        "processing_status": "failed", "page_status": "unreadable", "reasons": [], "reason_details": [],
        "page_id": None, "file_id": file_id, "job_key": job_key, "box_count": 0, "text_granularity": None,
        "geometry_correction": None, "corrected_width": None, "corrected_height": None,
        "mean_text_confidence": None, "render_same_as_rectified": None, "ocr_ms": None, "geometry_ms": None,
        "total_ms": None,
    }
    for key in _FILE_KEYS:
        entry[key], entry[f"{key}_sha256"] = None, None
    folder = _page_dir(ctx.cache_dir, page.image_id)
    (folder / ENTRY_NAME).unlink(missing_ok=True)  # no valid record exists while the files change
    files: dict[str, bytes] = {}
    merged_versions: dict[str, str] | None = None
    source = _inside(ctx.dataset_dir, page.file_name, f"page {page.image_id}")
    if not source.is_file():
        # M4 never saw this page. The reason is the builder's, not an M4 reason code.
        entry["reasons"] = ["source_file_missing"]
        entry["reason_details"] = [{"code": "source_file_missing", "message": f"{page.file_name} is not in the dataset"}]
    else:
        data = source.read_bytes()
        request = PageReadRequest(
            claim_id=claim_id, input_revision=1, job_key=job_key,
            # The manifest hash is passed as the recorded hash, so M4 itself refuses changed bytes.
            file=PageFileInput(file_id=file_id, media_type="image/jpeg", sha256=page.source_sha256, data=data,
                               page_count=1),
            versions=dict(ctx.versions),
            provenance={"source_kind": ctx.source_kind, "runtime_profile": ctx.runtime_profile,
                        "producer_service": PRODUCER_SERVICE, "source_dataset_id": ctx.dataset_name},
            object_uri_prefix=OBJECT_URI_PREFIX)
        result = run_page_reading(request, engine, ctx.config)
        if len(result.page_readings) != 1 or len(result.pages) > 1:
            # A refusal before any page (pinned version or provenance mismatch) is a build error.
            codes = ", ".join(f"{r.code}: {r.message}" for r in result.reasons) or "no page reading returned"
            raise M4CacheError(f"page {page.image_id}: M4 did not read exactly one page ({codes})")
        reading = result.page_readings[0]
        merged_versions = dict(reading["versions"])
        entry.update(processing_status=result.processing_status, page_status=reading["page_status"],
                     reasons=list(dict.fromkeys(r["code"] for r in reading["reasons"])),
                     reason_details=list(reading["reasons"]), page_id=reading["page_id"],
                     total_ms=result.metrics.get("total_ms"))
        artifacts = {a.key.rsplit("/", 1)[1]: a for a in result.artifacts}
        if result.pages:
            record = result.pages[0]
            metrics = result.metrics["pages"][0]
            files["document_page"] = _json_bytes(record.model_dump(mode="json"))
            files["page_reading"] = artifacts["page_reading.json"].data
            files["rectified"] = artifacts["rectified.png"].data
            same = artifacts["render.png"].data == files["rectified"]
            if not same:
                files["render"] = artifacts["render.png"].data
            if (_sha256(files["rectified"]) != record.corrected_render_ref.sha256
                    or _sha256(files["page_reading"]) != record.page_reading_ref.sha256):
                raise M4CacheError(f"page {page.image_id}: M4 artifacts do not match the page record's hashes")
            entry.update(box_count=len(record.text_boxes), text_granularity=record.text_granularity,
                         geometry_correction=record.transform.geometry_correction,
                         corrected_width=record.transform.corrected_width,
                         corrected_height=record.transform.corrected_height,
                         mean_text_confidence=record.mean_text_confidence, render_same_as_rectified=same,
                         ocr_ms=metrics["ocr_ms"], geometry_ms=metrics["geometry_ms"])
    names = {"document_page": "document_page.json", "page_reading": "page_reading.json",
             "rectified": "rectified.png", "render": "render.png"}
    for key, name in names.items():
        if key in files:
            _write_atomic(folder / name, files[key])
            entry[key] = f"pages/{page.image_id}/{name}"
            entry[f"{key}_sha256"] = _sha256(files[key])
        else:
            (folder / name).unlink(missing_ok=True)  # nothing of an earlier attempt outlives this one
    stored = {"schema": CACHE_SCHEMA, "fingerprint": ctx.fingerprint, "versions": merged_versions, "entry": entry}
    _write_atomic(folder / ENTRY_NAME, _json_bytes(stored))
    return stored


_WORKER: dict[str, Any] = {}


def _worker_start(ctx: _BuildContext, engine_factory: Callable[[], Any]) -> None:
    _WORKER.update(ctx=ctx, engine=engine_factory())


def _worker_read(page: DatasetPage) -> dict[str, Any]:
    return _read_page(_WORKER["ctx"], _WORKER["engine"], page)


def _environment() -> dict[str, Any]:
    found: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("numpy", "opencv-python-headless", "opencv-python", "pillow", "pydantic", "paddleocr",
                 "paddlepaddle"):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


def _git_state() -> tuple[str | None, bool | None]:
    """Commit and dirty flag of the repository, or ``(None, None)`` where git cannot say."""
    try:
        commit = subprocess.run(["git", "-C", str(_REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=30).stdout.strip()
        status = subprocess.run(["git", "-C", str(_REPO), "status", "--porcelain"], capture_output=True, text=True,
                                check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit, bool(status.strip())


def build_m4_cache(
    dataset_dir: str | Path,
    cache_dir: str | Path,
    *,
    config: Any,
    versions: Mapping[str, str],
    engine: Any | None = None,
    engine_factory: Callable[[], Any] | None = None,
    workers: int = 1,
    limit: int | None = None,
    image_ids: Iterable[str] | None = None,
    dataset_name: str | None = None,
    source_kind: str = "synthetic",
    runtime_profile: str = "lean",
    ocr_model_version: str | None = None,
    build_info: Mapping[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Read the selected pages with M4, keep its output and write ``manifest.json``.

    ``engine`` is used in this process. With ``workers`` above 1, ``engine_factory`` (a
    picklable callable) builds one engine in each worker process instead. ``limit`` and
    ``image_ids`` select which pages this call reads; pages cached by an earlier call under
    the same versions, configuration and M4 source stay in the manifest. ``build_info`` is
    merged into the manifest's ``m4`` block (container image, git commit, command).
    Returns the manifest.
    """
    from claim_cmev.documents.text_layout.adapter import M4_CODE_VERSION

    say = log or (lambda _message: None)
    dataset_root, cache_root = Path(dataset_dir), Path(cache_dir)
    name = dataset_name or dataset_root.name
    pages, dataset_info = read_dataset(dataset_root)
    by_id = {p.image_id: p for p in pages}
    wanted = list(image_ids) if image_ids is not None else None
    unknown = [i for i in wanted or [] if i not in by_id]
    if unknown:
        raise M4CacheError(f"image id(s) not in the dataset manifest: {', '.join(unknown)}")
    selected = [p for p in pages if wanted is None or p.image_id in set(wanted)]
    if limit is not None:
        selected = selected[:max(0, limit)]
    selected_ids = {p.image_id for p in selected}
    ctx = _BuildContext(dataset_root, cache_root, name, config, dict(versions), source_kind, runtime_profile,
                        _fingerprint(name, config, versions, source_kind, runtime_profile))
    cache_root.mkdir(parents=True, exist_ok=True)

    stored: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    todo: list[DatasetPage] = []
    for page in pages:
        record, is_stale = _stored_entry(ctx, page)
        if page.image_id in selected_ids:
            # A failure record is never reused: the page is read again on every run.
            if record is not None and record["entry"]["processing_status"] != "failed":
                stored[page.image_id] = record
            else:
                todo.append(page)
        elif record is not None:
            stored[page.image_id] = record
        elif is_stale:
            stale.append(page.image_id)
    say(f"{len(pages)} dataset pages, {len(selected)} selected, {len(selected) - len(todo)} already cached, "
        f"{len(todo)} to read")

    started = time.perf_counter()

    def done(index: int, record: dict[str, Any]) -> None:
        entry = record["entry"]
        stored[entry["image_id"]] = record
        ocr = "-" if entry["ocr_ms"] is None else f"{entry['ocr_ms']:.0f}ms"
        say(f"[{index}/{len(todo)}] {entry['image_id']} {entry['processing_status']} {entry['page_status']} "
            f"boxes={entry['box_count']} ocr={ocr} reasons={','.join(entry['reasons']) or '-'}")

    if todo and workers > 1:
        if engine_factory is None:
            raise ValueError("workers above 1 need engine_factory: each worker process builds its own engine")
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                     initializer=_worker_start, initargs=(ctx, engine_factory)) as pool:
                for index, record in enumerate(pool.map(_worker_read, todo), start=1):
                    done(index, record)
        except BrokenProcessPool as exc:
            # A killed worker is not a page result. Pages already written are kept; no manifest is written.
            raise M4CacheError(f"an OCR worker process died after {len(stored)} cached page(s), most often "
                               "from too little memory; run again to resume, with fewer workers") from exc
    elif todo:
        reader = engine if engine is not None else (engine_factory() if engine_factory else None)
        if reader is None:
            raise ValueError("an OCR engine or an engine_factory is required")
        for index, page in enumerate(todo, start=1):
            done(index, _read_page(ctx, reader, page))
    wall_seconds = round(time.perf_counter() - started, 1)

    entries = [stored[p.image_id]["entry"] for p in pages if p.image_id in stored]
    stamped = {json.dumps(stored[p.image_id]["versions"], sort_keys=True) for p in pages
               if p.image_id in stored and stored[p.image_id]["versions"]}
    if len(stamped) > 1:
        raise M4CacheError("cached pages carry more than one M4 versions map; rebuild into an empty folder")
    commit, dirty = _git_state()
    manifest = {
        "schema": CACHE_SCHEMA,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "complete": len(entries) == len(pages),
        "stale_pages": stale,
        "dataset": {"name": name, **dataset_info},
        "claim_id_derivation": "crockford_base32(sha256('<dataset name>:<dataset claim id>')[:16 bytes]), 26 characters",
        "m4": {
            "versions": json.loads(next(iter(stamped))) if stamped else None,
            "request_versions": dict(versions),
            "page_config_version": config.config_version,
            "page_config_sha256": config.sha256,
            "m4_code_version": M4_CODE_VERSION,
            "m4_source_sha256": m4_source_sha256(),
            "ocr_model_version": ocr_model_version or versions.get("ocr_weights"),
            "fingerprint": ctx.fingerprint,
            "producer_service": PRODUCER_SERVICE,
            "source_kind": source_kind,
            "runtime_profile": runtime_profile,
            "object_uri_prefix": OBJECT_URI_PREFIX,
            "container_image": None,
            "container_image_id": None,
            "git_commit": commit,
            "git_dirty": dirty,
            "environment": _environment(),
            **dict(build_info or {}),
        },
        "last_run": {"pages_read": len(todo), "pages_reused": len(selected) - len(todo), "workers": workers,
                     "wall_seconds": wall_seconds},
        "counts": {
            "pages": len(entries),
            "by_split": dict(sorted(Counter(e["split"] for e in entries).items())),
            "by_page_type": dict(sorted(Counter(e["page_type"] for e in entries).items())),
            "by_processing_status": dict(sorted(Counter(e["processing_status"] for e in entries).items())),
            "by_page_status": dict(sorted(Counter(e["page_status"] for e in entries).items())),
            "by_geometry_correction": dict(sorted(Counter(e["geometry_correction"] for e in entries
                                                          if e["geometry_correction"]).items())),
            "text_boxes": sum(e["box_count"] for e in entries),
        },
        "pages": entries,
    }
    data = _json_bytes(manifest)
    _write_atomic(cache_root / MANIFEST_NAME, data)
    say(f"manifest written: {len(entries)} of {len(pages)} pages, {manifest['counts']['text_boxes']} text boxes, "
        f"{wall_seconds}s for {len(todo)} page(s) read")
    return json.loads(data)


# --------------------------------------------------------------------------- loader

@dataclass(frozen=True)
class CachedPage:
    image_id: str
    dataset_claim_id: str
    claim_id: str
    split: str  # "train" | "validation" | "test"
    page_type: str  # "survey_assessment" | "repair_estimate"
    page_number: int  # the page's number inside its dataset claim; DocumentPage.page_number is 1
    processing_status: str
    page_status: str
    page: DocumentPage | None  # validated contract record; None only if M4 produced none
    rectified_path: Path | None  # absolute path to the corrected page image
    source_path: Path | None  # absolute path to the dataset JPEG when dataset_dir is given
    entry: Mapping[str, Any]  # the raw manifest entry


@dataclass(frozen=True)
class M4Cache:
    root: Path
    manifest: Mapping[str, Any]
    pages: tuple[CachedPage, ...]
    _index: dict[str, CachedPage] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {p.image_id: p for p in self.pages})

    def by_id(self, image_id: str) -> CachedPage:
        """The page with this dataset image id; ``KeyError`` when the cache has none."""
        return self._index[image_id]

    def split(self, name: str) -> tuple[CachedPage, ...]:
        """Pages of one dataset partition, in manifest order."""
        return tuple(p for p in self.pages if p.split == name)


def load_m4_cache(cache_dir: str | Path, dataset_dir: str | Path | None = None, *,
                  verify: str = "hashes", splits: tuple[str, ...] | None = None) -> M4Cache:
    """Load a cache written by ``build_m4_cache``.

    ``verify="none"`` parses the manifest and the page records only. ``verify="hashes"``
    also requires every listed file to exist and match its recorded SHA-256, the corrected
    image and the page reading to match the hashes inside the page record and, when
    ``dataset_dir`` is given, the dataset's ``manifest.csv`` and every source image to be
    the bytes the cache was built from. Any failure raises ``M4CacheError`` naming the page.
    ``splits`` restricts page loading and hashing before any page artifact is opened;
    the shared manifest remains available for provenance and partition metadata.
    """
    if verify not in VERIFY_MODES:
        raise ValueError(f"verify must be one of {VERIFY_MODES}, got {verify!r}")
    root = Path(os.path.abspath(cache_dir))
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise M4CacheError(f"no {MANIFEST_NAME} in {root}: the cache is absent or its build did not finish")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != CACHE_SCHEMA:
        raise M4CacheError(f"{manifest_path} has schema {manifest.get('schema')!r}; this loader reads {CACHE_SCHEMA!r}")
    dataset_root = Path(os.path.abspath(dataset_dir)) if dataset_dir is not None else None
    hashes = verify == "hashes"
    if hashes and dataset_root is not None:
        recorded, listing = manifest["dataset"]["manifest_sha256"], dataset_root / "manifest.csv"
        if not listing.is_file() or _file_sha256(listing) != recorded:
            raise M4CacheError(f"{listing} is not the manifest this cache was built from (sha256 {recorded})")
    pages = []
    for entry in manifest["pages"]:
        if splits is not None and entry["split"] not in splits:
            continue
        image_id = entry["image_id"]
        where = f"page {image_id}"
        paths: dict[str, Path | None] = {}
        for key in _FILE_KEYS:
            relative = entry.get(key)
            paths[key] = None if relative is None else _inside(root, relative, where)
            if hashes and paths[key] is not None:
                if not paths[key].is_file():
                    raise M4CacheError(f"{where}: {relative} is missing from the cache")
                if _file_sha256(paths[key]) != entry.get(f"{key}_sha256"):
                    raise M4CacheError(f"{where}: {relative} does not match its recorded SHA-256")
        record = None
        if paths["document_page"] is not None:
            try:
                record = DocumentPage.model_validate(json.loads(paths["document_page"].read_text(encoding="utf-8")))
            except FileNotFoundError as exc:
                raise M4CacheError(f"{where}: {entry['document_page']} is missing from the cache") from exc
            except ValueError as exc:
                raise M4CacheError(f"{where}: {entry['document_page']} is not a valid DocumentPage: {exc}") from exc
            if record.claim_id != entry["claim_id"]:
                raise M4CacheError(f"{where}: the page record belongs to another claim")
            if hashes and (record.corrected_render_ref.sha256 != entry["rectified_sha256"]
                           or record.page_reading_ref.sha256 != entry["page_reading_sha256"]):
                raise M4CacheError(f"{where}: cached files are not the artifacts the page record references")
        source_path = None
        if dataset_root is not None:
            source_path = _inside(dataset_root, entry["source_file"], where)
            if hashes and (not source_path.is_file() or _file_sha256(source_path) != entry["source_sha256"]):
                raise M4CacheError(f"{where}: {entry['source_file']} is missing from the dataset or is not the "
                                   "image the cache was built from")
        pages.append(CachedPage(
            image_id=image_id, dataset_claim_id=entry["dataset_claim_id"], claim_id=entry["claim_id"],
            split=entry["split"], page_type=entry["page_type"], page_number=entry["page_number"],
            processing_status=entry["processing_status"], page_status=entry["page_status"], page=record,
            rectified_path=paths["rectified"], source_path=source_path, entry=MappingProxyType(entry)))
    return M4Cache(root=root, manifest=MappingProxyType(manifest), pages=tuple(pages))


# --------------------------------------------------------------------------- coordinates

def _source_to_corrected(page: DocumentPage) -> np.ndarray:
    """Matrix from the stored image pixels of the uploaded file to M4's corrected page.

    ``DocumentPage.transform.homography`` maps the render frame (the image after EXIF
    orientation, where ``TextBox.quad_original`` lives) to the corrected frame, and
    ``homography_inverse`` maps back. The file's own pixel grid comes before EXIF
    orientation, so the orientation matrix is applied first.
    """
    transform = page.transform
    if transform.render_scale is not None:
        raise ValueError("this page was rasterised from a PDF; its source frame is PDF points, not image pixels")
    orientation = transform.exif_orientation or 1
    # source_width/height describe the oriented render; orientations 5-8 swap the stored axes.
    stored_width, stored_height = oriented_size(orientation, transform.source_width, transform.source_height)
    return np.asarray(transform.homography, dtype=float) @ orientation_matrix(orientation, stored_width, stored_height)


def source_to_corrected_points(page: DocumentPage, points: Any) -> np.ndarray:
    """Map ``[N, 2]`` pixel points of the ORIGINAL image file into M4's corrected-page frame."""
    return map_points(_source_to_corrected(page), points)


def source_to_corrected_boxes(page: DocumentPage, boxes_xyxy: Any) -> np.ndarray:
    """Map axis-aligned pixel boxes of the ORIGINAL image file into M4's corrected-page frame.

    The original frame is the pixel grid of the dataset image as stored, before EXIF
    orientation. The four corners of each box are warped with the page transform and
    bounded again, so the result is axis-aligned. Returns a float array ``[N, 4]``.
    """
    boxes = np.asarray(boxes_xyxy, dtype=float)
    if boxes.size == 0:
        return np.zeros((0, 4), dtype=float)
    if boxes.ndim == 1:
        boxes = boxes[None, :]
    if boxes.ndim != 2 or boxes.shape[1] != 4:
        raise ValueError(f"boxes must be [N, 4] as x_min, y_min, x_max, y_max; got shape {boxes.shape}")
    x0, y0, x1, y1 = boxes.T
    corners = np.stack([x0, y0, x1, y0, x1, y1, x0, y1], axis=1).reshape(-1, 2)
    warped = source_to_corrected_points(page, corners).reshape(-1, 4, 2)
    return np.concatenate([warped.min(axis=1), warped.max(axis=1)], axis=1)


def corrected_boxes_to_norm(page: DocumentPage, boxes_xyxy: Any) -> np.ndarray:
    """Corrected-frame pixel boxes to ``[0, 1]`` coordinates, as M4 computes ``TextBox.box_norm``.

    M4 rounds the stored value to six decimals; this returns the unrounded floats.
    """
    boxes = np.asarray(boxes_xyxy, dtype=float).reshape(-1, 4)
    size = np.array([page.transform.corrected_width, page.transform.corrected_height] * 2, dtype=float)
    return np.clip(boxes / size, 0.0, 1.0)


# --------------------------------------------------------------------------- command line

def paddle_engine() -> Any:
    """The served PaddleOCR engine, from the verified registry entry; one per process."""
    from claim_cmev.documents.text_layout.config import load_page_reading_config
    from claim_cmev.documents.text_layout.model_config import load_ocr_model_config, verify_ocr_registry
    from claim_cmev.documents.text_layout.paddle import PaddleOcrEngine

    return PaddleOcrEngine.from_config(verify_ocr_registry(load_ocr_model_config()), load_page_reading_config())


def _summary(manifest: Mapping[str, Any]) -> str:
    counts = manifest["counts"]
    return (f"{counts['pages']} of {manifest['dataset']['page_count']} pages; splits {counts['by_split']}; "
            f"page types {counts['by_page_type']}; page status {counts['by_page_status']}; "
            f"geometry {counts['by_geometry_correction']}; {counts['text_boxes']} text boxes")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelines.documents.m4_cache", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="read dataset pages with M4 and cache its output")
    build.add_argument("--dataset", required=True, type=Path, help="dataset folder holding manifest.csv")
    build.add_argument("--cache", required=True, type=Path, help="cache folder to write or verify")
    build.add_argument("--limit", type=int, help="read only the first N selected pages")
    build.add_argument("--image-id", action="append", dest="image_ids", metavar="ID", help="read only these pages")
    build.add_argument("--workers", type=int, default=1, help="OCR processes; each loads its own engine")
    build.add_argument("--verify-only", action="store_true", help="check an existing cache; read nothing")
    build.add_argument("--dataset-name", help="recorded dataset name (default: the dataset folder's name)")
    build.add_argument("--source-kind", default="synthetic", choices=("real", "synthetic"))
    build.add_argument("--runtime-profile", default="lean", choices=("lean", "full"))
    build.add_argument("--container-image", help="recorded as given: the image this command runs in")
    build.add_argument("--container-image-id", help="recorded as given")
    build.add_argument("--git-commit", help="recorded as given, where git cannot be run")
    build.add_argument("--git-dirty", choices=("true", "false"), help="recorded as given, where git cannot be run")
    build.add_argument("--command-line", help="recorded as given: the host command that started this build")
    args = parser.parse_args(argv)

    if args.verify_only:
        try:
            cache = load_m4_cache(args.cache, args.dataset, verify="hashes")
        except M4CacheError as exc:
            print(f"cache verification FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"cache verified: {_summary(cache.manifest)}; complete={cache.manifest['complete']}")
        return 0

    import claim_cmev
    from claim_cmev.documents.text_layout.config import load_page_reading_config
    from claim_cmev.documents.text_layout.model_config import load_ocr_model_config
    from claim_cmev.orchestration.plan import CODE_VERSION

    model = load_ocr_model_config()
    config = load_page_reading_config()
    print(f"claim_cmev imported from {Path(claim_cmev.__file__).resolve().parent}", flush=True)
    info: dict[str, Any] = {}
    for key, value in (("container_image", args.container_image), ("container_image_id", args.container_image_id),
                       ("git_commit", args.git_commit), ("command_line", args.command_line)):
        if value is not None:
            info[key] = value
    if args.git_dirty is not None:
        info["git_dirty"] = args.git_dirty == "true"
    manifest = build_m4_cache(
        args.dataset, args.cache, config=config, versions=model.stage_versions(CODE_VERSION),
        engine_factory=paddle_engine, workers=args.workers, limit=args.limit, image_ids=args.image_ids,
        dataset_name=args.dataset_name, source_kind=args.source_kind, runtime_profile=args.runtime_profile,
        ocr_model_version=model.model_version, build_info=info, log=lambda message: print(message, flush=True))
    print(_summary(manifest), flush=True)
    return 0


__all__ = ["CACHE_SCHEMA", "CachedPage", "DatasetPage", "M4Cache", "M4CacheError", "build_m4_cache", "claim_id_for",
           "corrected_boxes_to_norm", "load_m4_cache", "m4_source_sha256", "paddle_engine", "read_dataset",
           "source_to_corrected_boxes", "source_to_corrected_points"]

if __name__ == "__main__":
    raise SystemExit(main())
