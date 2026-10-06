"""Render the documentation drawings with Graphviz; no model/data dependencies.

Run from any directory: python3 docs/diagrams/render_vision_workflow.py
Exports stay in the repository's ignored artifacts/exports/ directory.
"""
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


def main():
    sources = Path(__file__).resolve().parent
    destination = sources.parents[1] / "artifacts" / "exports" / "vision-workflow"
    dot = shutil.which("dot")
    if dot is None:
        raise SystemExit("Graphviz 'dot' is required to render these documentation sources.")
    destination.mkdir(parents=True, exist_ok=True)
    diagrams = {
        "TENSOR": "vision-tensor-workflow",
        "EXAMPLE": "vision-summary-example",
    }
    viewer = (sources / "vision-workflow-viewer.html").read_text()
    for key, stem in diagrams.items():
        for extension in ("svg", "pdf", "png"):
            result = subprocess.run(
                [dot, f"-T{extension}", "-Gdpi=110", str(sources / f"{stem}.dot"),
                 "-o", str(destination / f"{stem}.{extension}")],
                check=True, capture_output=True, text=True,
            )
            if result.stderr:
                raise SystemExit(f"Graphviz reported a rendering issue: {result.stderr}")
        svg_file = destination / f"{stem}.svg"
        ET.parse(svg_file)
        svg = svg_file.read_text()
        svg = svg[svg.index("<svg"):]
        # Graphviz reuses IDs across diagrams. Prefix them in the combined viewer.
        svg = svg.replace('id="', f'id="{key.lower()}-')
        viewer = viewer.replace(f"@@{key}_SVG@@", svg)
        print(f"Rendered {stem}: SVG, PDF, PNG")
    if "@@" in viewer:
        raise SystemExit("An SVG placeholder was not replaced.")
    (destination / "index.html").write_text(viewer)
    print(f"Self-contained viewer: {destination / 'index.html'}")


if __name__ == "__main__":
    main()
