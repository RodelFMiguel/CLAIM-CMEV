#!/usr/bin/env python3
"""Download original dataset assets without conversion or extraction (Python 3.9+)."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/manifests/dataset_sources.json"
CHUNK = 1024 * 1024


class DownloadError(Exception):
    """A safe diagnostic that must never contain a credential or private URL."""


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise DownloadError("Refused a redirect away from HTTPS.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
            redirected.remove_header("Cookie")
        return redirected


OPENER = build_opener(SafeRedirect())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root, name):
    """Reject traversal and platform-specific special paths before writing."""
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith("/") or "\\" in name or ":" in name
            or any(p in (".", "..") or p.endswith((".", " ")) for p in parts)):
        raise DownloadError("Invalid output filename.")
    target = root.joinpath(*parts)
    for parent in (target, *target.parents):
        if parent == root.parent:
            break
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise DownloadError("Output contains a symbolic link or junction.")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise DownloadError("Output escapes the dataset directory.") from None
    return target


@contextmanager
def dataset_lock(directory):
    lock = directory / ".download.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise DownloadError("Dataset is locked; after checking no downloader is running, remove its .download.lock.") from None
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink()


def validate_file(path, expected=None):
    if not path.stat().st_size:
        raise DownloadError("Downloaded file is empty.")
    with path.open("rb") as stream:
        prefix = stream.read(256).lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<?xml", b"<error")):
        raise DownloadError("Server returned an HTML/XML page instead of dataset content.")
    # Check ZIP structure, including CRCs, before publishing completed archives.
    if path.name.endswith((".zip", ".zip.part")):
        try:
            with zipfile.ZipFile(path) as archive:
                if not archive.infolist() or archive.testzip() is not None:
                    raise DownloadError("ZIP archive is empty or has a CRC failure.")
        except zipfile.BadZipFile:
            raise DownloadError("Invalid ZIP archive.") from None
    digest = sha256(path)
    if expected and digest != expected.lower():
        raise DownloadError("SHA-256 mismatch; existing source is preserved.")
    return {"sha256": digest, "bytes": path.stat().st_size}


def http_download(url, target, expected=None, retries=3, timeout=60):
    if urlsplit(url).scheme != "https" or urlsplit(url).username:
        raise DownloadError("Download sources must be HTTPS URLs without embedded user credentials.")
    partial = target.with_name(target.name + ".part")
    state_path = target.with_name(target.name + ".partial.json")
    identity = hashlib.sha256(url.encode()).hexdigest()
    for attempt in range(retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        state = read_json(state_path) if state_path.exists() else {}
        headers = {"User-Agent": "CLAIM-CMEV-dataset-downloader/1", "Accept-Encoding": "identity"}
        # Resume only when a validator ties the partial bytes to this exact source.
        resume = offset and state.get("source_hash") == identity and state.get("validator")
        if resume:
            headers.update({"Range": f"bytes={offset}-", "If-Range": state["validator"]})
        else:
            offset = 0
        try:
            with OPENER.open(Request(url, headers=headers), timeout=timeout) as response:
                status = response.status
                if status not in (200, 206):
                    raise DownloadError("Unexpected HTTP response.")
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not resume or not match or int(match[1]) != offset:
                        raise DownloadError("Invalid resume response; partial source is preserved.")
                    total = int(match[3])
                    response_validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
                    if response_validator != state["validator"]:
                        raise DownloadError("Source changed during resume; partial source is preserved.")
                else:
                    offset = 0
                    total = int(response.headers.get("Content-Length", 0)) or None
                validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
                if validator and validator.startswith("W/"):
                    validator = response.headers.get("Last-Modified")
                save_json(state_path, {"source_hash": identity, "validator": validator})
                with partial.open("ab" if offset else "wb") as stream:
                    for block in iter(lambda: response.read(CHUNK), b""):
                        stream.write(block)
                if total is not None and partial.stat().st_size != total:
                    raise OSError("Incomplete transfer")
            metadata = validate_file(partial, expected)
            partial.replace(target)
            state_path.unlink(missing_ok=True)
            return metadata
        except HTTPError as error:
            if error.code == 416 and attempt < retries:
                # Complete or stale partial: restart, never accept a 416 as success.
                state_path.unlink(missing_ok=True)
            elif error.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise DownloadError(f"HTTP {error.code}; check access, source availability and credentials.") from None
        except (OSError, URLError, http.client.HTTPException):
            if attempt == retries:
                raise DownloadError("Transfer failed; partial bytes retained for retry.") from None
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))
    raise DownloadError("Transfer did not complete.")


def acquire_file(spec, directory, retries, timeout):
    name = spec["name"]
    target = safe_path(directory, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = safe_path(directory, name + ".receipt.json")
    safe_path(directory, name + ".part")
    safe_path(directory, name + ".partial.json")
    if name in ("acquisition.json", ".download.lock") or name.endswith((".part", ".receipt.json", ".partial.json", ".tmp")):
        raise DownloadError("Filename collides with downloader metadata.")
    # Never serialize the URL: DocILE tokens occur in URL paths; signed links in queries.
    locator = spec.get("url") or spec.get("path")
    if not locator or bool(spec.get("url")) == bool(spec.get("path")):
        raise DownloadError("Each configured file needs exactly one of url or path.")
    source_hash = hashlib.sha256(locator.encode()).hexdigest()
    if target.exists():
        if not receipt_path.exists():
            raise DownloadError("Existing file has no receipt; configure it as a local source in a new output directory.")
        receipt = read_json(receipt_path)
        if receipt["source_hash"] != source_hash:
            raise DownloadError("Source locator changed; use a new output directory to preserve the old acquisition.")
        metadata = validate_file(target, spec.get("sha256") or receipt["sha256"])
        if metadata["sha256"] != receipt["sha256"]:
            raise DownloadError("Existing file changed since acquisition.")
        return receipt
    if "path" in spec:
        source = Path(spec["path"]).expanduser()
        if not source.is_file():
            raise DownloadError("Configured local source file is missing.")
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(source, partial)
        metadata = validate_file(partial, spec.get("sha256"))
        partial.replace(target)
    else:
        metadata = http_download(spec["url"], target, spec.get("sha256"), retries, timeout)
    receipt = dict(metadata, file=name, source_hash=source_hash,
                   acquired_at=datetime.now(timezone.utc).isoformat(),
                   publisher_checksum_supplied=bool(spec.get("sha256")))
    save_json(receipt_path, receipt)
    return receipt


def prerequisites(dataset, local):
    if dataset.get("needs_evidence") and not local.get("permission_evidence"):
        return "needs-access-evidence"
    if local.get("files"):
        return "ready-local-sources"
    provider = dataset["provider"]
    if provider == "manual":
        return "needs-local-sources"
    if provider == "docile" and not os.environ.get("DOCILE_TOKEN"):
        return "needs-DOCILE_TOKEN"
    if dataset.get("token_required") and not os.environ.get("HF_TOKEN"):
        return "needs-HF_TOKEN-and-approved-access"
    if provider == "kaggle" and not shutil.which("kaggle"):
        return "needs-kaggle-cli"
    if provider == "huggingface":
        import importlib.util
        if importlib.util.find_spec("huggingface_hub") is None:
            return "needs-huggingface_hub"
    return "ready"


def docile_files(extras):
    subsets = ["annotated-trainval", "test"]
    if extras:
        subsets += ["synthetic", "unlabeled-annotations"]
        subsets += [f"unlabeled-chunk-{i:02d}" for i in range(94)]
    token = quote(os.environ["DOCILE_TOKEN"], safe="")
    return [{"name": f"{s}.zip",
             "url": f"https://docile-dataset-rossum.s3.eu-west-1.amazonaws.com/{token}/{s}.zip",
             "provenance": "synthetic" if s == "synthetic" else "real"} for s in subsets]


def external_download(dataset, directory):
    """Use official provider clients; suppress errors that could expose tokens."""
    output = directory / "files"
    output.mkdir(exist_ok=True)
    provider = dataset["provider"]
    try:
        if provider == "huggingface":
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=dataset["repo"], repo_type="dataset",
                              revision=dataset["revision"], local_dir=str(output),
                              token=os.environ.get("HF_TOKEN") or False, max_workers=4)
        elif provider == "kaggle":
            # CLI manages authentication. Never put credentials on the command line.
            result = subprocess.run(
                ["kaggle", "datasets", "download", "-d", dataset["repo"], "-p", str(output)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if result.returncode:
                raise DownloadError("Kaggle download failed; check CLI authentication and source access.")
    except DownloadError:
        raise
    except Exception:
        raise DownloadError("Provider download failed; check client installation and authorized access.") from None
    assets = sorted(p for p in output.rglob("*") if p.is_file()
                    and ".cache" not in p.relative_to(output).parts)
    if not assets:
        raise DownloadError("Provider returned no files.")
    return [dict(validate_file(p), file=p.relative_to(directory).as_posix(),
                 acquired_at=datetime.now(timezone.utc).isoformat(),
                 publisher_checksum_supplied=False) for p in assets]


def acquire_dataset(dataset, local, directory, args):
    status = prerequisites(dataset, local)
    if not status.startswith("ready"):
        return {"id": dataset["id"], "status": status, "next_step": dataset["access"]}
    directory.mkdir(parents=True, exist_ok=True)
    with dataset_lock(directory):
        manifest_path = directory / "acquisition.json"
        configuration_hash = hashlib.sha256(json.dumps(
            {"dataset": dataset, "local": local, "docile_extras": args.docile_extras},
            sort_keys=True).encode()).hexdigest()
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            if manifest["configuration_hash"] != configuration_hash:
                raise DownloadError("Acquisition configuration changed; use a new output root.")
            for asset in manifest["files"]:
                validate_file(safe_path(directory, asset["file"]), asset["sha256"])
            return {"id": dataset["id"], "status": "verified-existing", "files": len(manifest["files"])}
        files = local.get("files")
        if not files:
            files = docile_files(args.docile_extras) if dataset["provider"] == "docile" else dataset.get("files")
        if files:
            receipts = []
            for index, spec in enumerate(files, 1):
                print(f"  {dataset['id']}: file {index}/{len(files)}", flush=True)
                receipt = acquire_file(spec, directory, args.retries, args.timeout)
                receipts.append(dict(receipt, provenance=spec.get("provenance", dataset["provenance"])))
        else:
            receipts = external_download(dataset, directory)
        manifest = {"dataset_id": dataset["id"], "source_page": dataset["source"],
                    "revision": dataset.get("revision", "unversioned; acquisition identified by file hashes"),
                    "license_note": dataset["license"], "provenance": dataset["provenance"],
                    "configuration_hash": configuration_hash,
                    "permission_evidence_recorded": bool(local.get("permission_evidence")),
                    "acquired_at": datetime.now(timezone.utc).isoformat(), "files": receipts,
                    "status": "acquired", "usable_samples": None,
                    "annotations_validated": False, "splits_created": False,
                    "downloader_sha256": sha256(Path(__file__)),
                    "catalogue_sha256": sha256(CATALOG)}
        if dataset["id"] == "docile" and args.docile_extras:
            manifest["provenance"] = "mixed; see per-file provenance"
        save_json(manifest_path, manifest)
    return {"id": dataset["id"], "status": "acquired", "files": len(receipts)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", help="Dataset IDs; default is all non-reserve entries.")
    parser.add_argument("--include-reserve", action="store_true")
    parser.add_argument("--list", action="store_true", help="Show catalogue and prerequisites; no downloads/writes.")
    parser.add_argument("--dry-run", action="store_true", help="Alias for --list.")
    parser.add_argument("--output", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--sources", type=Path, default=ROOT / "runtime/dataset-sources.local.json")
    parser.add_argument("--docile-extras", action="store_true", help="Include all synthetic and unlabeled DocILE archives.")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    if args.retries < 0 or args.timeout <= 0:
        parser.error("retries must be nonnegative and timeout must be positive")
    catalogue = read_json(CATALOG)["datasets"]
    known = {d["id"] for d in catalogue}
    if args.datasets and set(args.datasets) - known:
        parser.error("Unknown dataset IDs: " + ", ".join(sorted(set(args.datasets) - known)))
    if args.sources.exists():
        try:
            local = read_json(args.sources)
        except (OSError, ValueError):
            parser.error("Cannot read local sources as JSON")
        if not isinstance(local, dict) or set(local) - known:
            parser.error("Local sources must be an object keyed by known dataset IDs")
    else:
        if args.sources != ROOT / "runtime/dataset-sources.local.json":
            parser.error("Explicit --sources file does not exist")
        local = {}
    for settings in local.values():
        if not isinstance(settings, dict):
            parser.error("Each dataset's local settings must be an object")
        if set(settings) - {"files", "permission_evidence"}:
            parser.error("Unknown local setting; expected files or permission_evidence")
        evidence = settings.get("permission_evidence", "")
        if not isinstance(evidence, str):
            parser.error("permission_evidence must be a string")
        settings["permission_evidence"] = evidence.strip()
        if "files" in settings:
            if not isinstance(settings["files"], list) or not settings["files"]:
                parser.error("files must be a non-empty list")
            names = set()
            for item in settings["files"]:
                if not isinstance(item, dict) or set(item) - {"name", "url", "path", "sha256"}:
                    parser.error("File entries support name, url or path, and optional sha256")
                if not isinstance(item.get("name"), str) or item["name"] in names:
                    parser.error("File names must be unique strings within each dataset")
                if bool(item.get("url")) == bool(item.get("path")):
                    parser.error("Each file needs exactly one of url or path")
                if not all(isinstance(value, str) for value in item.values()):
                    parser.error("File entry values must be strings")
                if item.get("sha256") and not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]):
                    parser.error("sha256 must contain exactly 64 hexadecimal characters")
                try:
                    safe_path(args.output, item["name"])
                except DownloadError:
                    parser.error("Invalid output filename in local sources")
                names.add(item["name"])
    selected = [d for d in catalogue if (d["id"] in args.datasets if args.datasets
                else args.include_reserve or d["role"] != "reserve")]
    if args.list or args.dry_run:
        for dataset in selected:
            print(f"{dataset['id']:16} {dataset['role']:14} {prerequisites(dataset, local.get(dataset['id'], {}))}")
            print(f"  {dataset['access']}")
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for dataset in selected:
        print(f"{dataset['id']}: starting", flush=True)
        try:
            result = acquire_dataset(dataset, local.get(dataset["id"], {}),
                                     safe_path(args.output, dataset["id"]), args)
        except DownloadError as error:
            result = {"id": dataset["id"], "status": "failed", "detail": str(error)}
        except (OSError, ValueError, KeyError, TypeError):
            result = {"id": dataset["id"], "status": "failed",
                      "detail": "Invalid local configuration, receipt, or filesystem state; inspect local inputs."}
        results.append(result)
        print(f"{dataset['id']}: {result['status']}", flush=True)
        if result.get("detail") or result.get("next_step"):
            print("  " + result.get("detail", result.get("next_step", "")))
    report = args.output / ("download-report-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    save_json(report, {"created_at": datetime.now(timezone.utc).isoformat(), "datasets": results})
    print(f"Report: {report}")
    return 0 if all(r["status"] in ("acquired", "verified-existing") for r in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; completed files and resumable partial downloads retained.")
        raise SystemExit(130)
