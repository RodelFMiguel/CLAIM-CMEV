"""Conventions of the tracked M6 training notebook; nothing is executed and no data is read.

The notebook is checked as a file: it must be valid, carry no outputs, keep its opt-in
switches off, and reach the held-out test split only inside the ``FINAL_EVAL`` branch.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")

NOTEBOOK = Path(__file__).resolve().parents[3] / "notebooks" / "M6_Document_Worker.ipynb"


@pytest.fixture(scope="module")
def notebook():
    if not NOTEBOOK.is_file():
        pytest.skip(f"{NOTEBOOK.name} is not in this checkout")
    return nbformat.read(NOTEBOOK, as_version=4)


def _code(notebook) -> list[str]:
    return [cell.source for cell in notebook.cells if cell.cell_type == "code"]


def test_the_notebook_is_valid_and_has_the_project_kernel_and_no_outputs(notebook):
    nbformat.validate(notebook)
    assert notebook.nbformat == 4
    assert dict(notebook.metadata["kernelspec"]) == {
        "display_name": "CLAIM-CMEV (Python 3.12)", "language": "python", "name": "claim-cmev"}
    for cell in notebook.cells:
        if cell.cell_type == "code":
            assert cell.outputs == [] and cell.execution_count is None


def test_every_code_cell_parses_and_follows_a_markdown_cell(notebook):
    previous = None
    for cell in notebook.cells:
        if cell.cell_type == "code":
            ast.parse(cell.source)
            assert previous is not None and previous.cell_type == "markdown", cell.source[:60]
        previous = cell


def test_the_opt_in_switches_are_off_in_one_configuration_cell(notebook):
    switches = ("QUICK_RUN = False", "FINAL_EVAL = False", "EXPORT_FOR_SERVING = False", "DEVICE = None",
                "RELOAD_RUN_DIR = None", "RESUME_RUN_DIR = None")
    holders = [source for source in _code(notebook) if all(switch in source for switch in switches)]
    assert len(holders) == 1, "the switches live together in exactly one cell"
    everything = "\n".join(_code(notebook))
    for switch in ("QUICK_RUN = True", "FINAL_EVAL = True", "EXPORT_FOR_SERVING = True"):
        assert switch not in everything
    assert "INSTALL_DEPENDENCIES = False" in everything and "caffeinate" not in everything


def test_the_test_split_is_read_only_inside_the_final_evaluation_branch(notebook):
    readers = [source for source in _code(notebook) if "include_test=True" in source]
    assert len(readers) == 1
    tree = ast.parse(readers[0])
    guarded = [node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
               and node.test.id == "FINAL_EVAL"]
    assert len(guarded) == 1
    inside = ast.get_source_segment(readers[0], guarded[0])
    outside = readers[0].replace(inside, "")
    assert "include_test" in "".join(ast.unparse(node) for node in guarded[0].body)
    assert "include_test" not in outside and '"test"' not in outside
    others = "\n".join(source for source in _code(notebook) if source is not readers[0])
    assert 'splits=("test"' not in others and "marks_coco_test" not in others


def test_the_export_is_opt_in_and_configuration_files_are_never_written(notebook):
    everything = "\n".join(_code(notebook))
    exports = [source for source in _code(notebook) if "registry_root=REGISTRY_ROOT" in source]
    assert len(exports) == 1 and "if EXPORT_FOR_SERVING:" in exports[0]
    assert "m6_linking.yaml" not in "".join(line for line in everything.splitlines()
                                            if "open(" in line or "write_text" in line)
    assert 'pmd.AugmentConfig()' in everything and "horizontal" not in everything.lower()
