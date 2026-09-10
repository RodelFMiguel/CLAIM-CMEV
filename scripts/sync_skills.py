#!/usr/bin/env python3
"""Keep the seven repository skill sources and Claude Code copies in sync."""

import argparse
from pathlib import Path
import re
import sys


SKILL_NAMES = (
    "cmev-implement-module",
    "cmev-change-contract",
    "cmev-prepare-dataset",
    "cmev-run-experiment",
    "cmev-review-evidence-logic",
    "cmev-refresh-cost-reference",
    "cmev-verify-workbench",
)


def _check_path(path: Path, root: Path) -> None:
    """Reject managed paths through symlinks/junctions or outside this root."""
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as error:
        raise ValueError(f"Path outside repository: {path}") from error
    current = root
    for part in relative.parts:
        current = current / part
        is_junction = getattr(current, "is_junction", lambda: False)
        if current.is_symlink() or is_junction():
            raise ValueError(f"Managed path is a link: {current}")
        if current.exists():
            try:
                current.resolve().relative_to(root)
            except ValueError as error:
                raise ValueError(f"Path resolves outside repository: {current}") from error


def _read_tree(folder: Path, root: Path, *, required: bool) -> dict:
    _check_path(folder, root)
    if not folder.exists():
        if required:
            raise ValueError(f"Missing canonical skill directory: {folder}")
        return {}
    if not folder.is_dir():
        raise ValueError(f"Expected skill directory: {folder}")
    result = {}
    for path in sorted(folder.rglob("*")):
        _check_path(path, root)
        if path.is_file():
            result[path.relative_to(folder)] = path.read_bytes()
        elif not path.is_dir():
            raise ValueError(f"Unsupported managed file type: {path}")
    return result


def _check_entrypoint(files: dict, name: str) -> None:
    entrypoint = files.get(Path("SKILL.md"))
    if entrypoint is None:
        raise ValueError(f"Missing SKILL.md in canonical skill: {name}")
    text = entrypoint.decode("utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"Missing YAML frontmatter in {name}")
    header = text[4:].split("\n---\n", 1)[0]
    match = re.search(r"""(?m)^name:\s*['"]?([a-z0-9-]+)['"]?\s*$""", header)
    if not match or match.group(1) != name:
        raise ValueError(f"Frontmatter name does not match directory: {name}")
    if not re.search(r"(?m)^description:\s*\S", header):
        raise ValueError(f"Missing frontmatter description in {name}")
    # This is a sync precondition check, not a full YAML/Agent Skills validator.


def synchronize(root: Path, *, check: bool) -> list:
    """Return drift paths; in write mode update them after complete preflight."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Repository root is not a directory: {root}")
    planned = []
    for name in SKILL_NAMES:
        source = root / ".agents" / "skills" / name
        destination = root / ".claude" / "skills" / name
        source_files = _read_tree(source, root, required=True)
        _check_entrypoint(source_files, name)
        destination_files = _read_tree(destination, root, required=False)
        stale = set(destination_files) - set(source_files)
        if stale:
            paths = ", ".join(str(destination / item) for item in sorted(stale))
            raise ValueError(f"Stale mirror files; reconcile explicitly: {paths}")
        for relative, content in sorted(source_files.items()):
            target = destination / relative
            _check_path(target, root)
            if target.exists() and not target.is_file():
                raise ValueError(f"Expected mirror file: {target}")
            if destination_files.get(relative) != content:
                planned.append((target, content))

    # No directories or files are changed until all seven sources pass preflight.
    if not check:
        for target, content in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return [path.relative_to(root) for path, _ in planned]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report drift without writing")
    mode.add_argument("--write", action="store_true", help="Create/update managed copies")
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        changed = synchronize(root, check=arguments.check)
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Skill sync failed: {error}", file=sys.stderr)
        return 2
    if arguments.check and changed:
        print("Skill mirrors need synchronisation:")
        for path in changed:
            print(f"  {path.as_posix()}")
        print("Run: python3 scripts/sync_skills.py --write")
        return 1
    if arguments.check:
        print(f"All {len(SKILL_NAMES)} skill mirrors match their canonical sources.")
    else:
        print(f"Updated {len(changed)} files across {len(SKILL_NAMES)} managed skills.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
