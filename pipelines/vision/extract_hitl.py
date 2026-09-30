"""Extract supplied HITL ZIPs to a new directory, preserving source archives."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile


def extract_archives(archives: list[Path], destination: Path) -> Path:
    """Validate all entries, extract in staging, then publish without overwrite.

    Each archive gets a separate directory so swapped/duplicate publisher folder
    names cannot overwrite one another. Class metadata determines the task later.
    """
    archives = [Path(p).resolve() for p in archives]
    destination = Path(destination).absolute()
    if not archives or len(set(archives)) != len(archives):
        raise ValueError("Supply one or more distinct publisher ZIP archives")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    for archive in archives:
        with zipfile.ZipFile(archive) as source:
            seen: set[str] = set()
            for entry in source.infolist():
                name = PurePosixPath(entry.filename)
                if (name.is_absolute() or not name.parts or
                    any(p in ("..", ".") for p in name.parts) or
                    "\\" in entry.filename or ":" in entry.filename or
                    stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ValueError(f"Unsafe ZIP entry in {archive.name}")
                normalized = str(name).casefold()
                if normalized in seen:
                    raise ValueError(f"Duplicate ZIP entry in {archive.name}")
                seen.add(normalized)
            bad = source.testzip()
            if bad is not None:
                raise ValueError(f"Corrupt ZIP archive: {archive.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".hitl-extract-", dir=destination.parent))
    try:
        receipts = []
        for index, archive in enumerate(archives):
            with zipfile.ZipFile(archive) as source:
                source.extractall(staging / f"archive-{index + 1}")
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            receipts.append({"archive": str(archive), "sha256": digest})
        (staging / "extraction.json").write_text(json.dumps({
            "archives": receipts, "policy": "originals-preserved; separate archive roots"
        }, indent=2) + "\n")
        if destination.exists():
            raise FileExistsError(destination)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(extract_archives(args.archives, args.output))
