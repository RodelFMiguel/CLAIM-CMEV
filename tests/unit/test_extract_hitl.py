from pathlib import Path
import zipfile

import pytest

from pipelines.vision.extract_hitl import extract_archives


def test_extract_preserves_archives_and_separates_roots(tmp_path):
    archives = []
    for index in range(2):
        archive = tmp_path / f"{index}.zip"
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("dataset/meta.json", str(index))
        archives.append(archive)
    output = extract_archives(archives, tmp_path / "extracted")
    assert (output / "archive-1/dataset/meta.json").read_text() == "0"
    assert (output / "archive-2/dataset/meta.json").read_text() == "1"
    assert all(p.exists() for p in archives)
    with pytest.raises(FileExistsError):
        extract_archives(archives, output)


@pytest.mark.parametrize("name", ["../escaped", "/escaped", "C:/escaped", "x\\escaped"])
def test_rejects_unsafe_paths_before_publishing(tmp_path: Path, name: str):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr(name, "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        extract_archives([archive], tmp_path / "extracted")
    assert not (tmp_path / "extracted").exists()
