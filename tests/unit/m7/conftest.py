"""Shared M7 fixtures: one real synthetic build per session, written under pytest's tmp area."""
from __future__ import annotations

from pathlib import Path
import shutil
import stat

import pytest

from claim_cmev.costs.reference.build import run

SEED = 20260924


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """(registry_root, manifest) for the demo build with the frozen configuration."""
    root = tmp_path_factory.mktemp("registry")
    manifest = run(["--seed", str(SEED), "--out", str(root), "--promote", "--with-injected"])
    return root, manifest


@pytest.fixture
def writable_copy(built, tmp_path):
    """A writable copy of the published table for tamper tests; the original stays untouched."""
    root, manifest = built

    def make(name: str = "copy") -> tuple[Path, str]:
        version = manifest["table_version"]
        target_root = tmp_path / name
        shutil.copytree(root / version, target_root / version)
        for path in (target_root / version).iterdir():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return target_root, version

    return make
