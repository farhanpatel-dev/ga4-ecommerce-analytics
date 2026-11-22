"""Turn notebooks/src/*.py (percent format) into executed notebooks/*.ipynb.

The .py sources are what gets reviewed in diffs; the .ipynb files are generated, executed (so
outputs and figures render on GitHub) and committed alongside.

Cell markers:  "# %%" starts a code cell, "# %% [markdown]" starts a markdown cell whose lines
are written as "# text".

Usage:
    python scripts/build_notebooks.py            # all notebooks
    python scripts/build_notebooks.py 02_funnel  # one notebook
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "notebooks" / "src"
OUT = ROOT / "notebooks"


def parse(path: Path) -> nbformat.NotebookNode:
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    kind, lines = None, []

    def flush():
        text = "\n".join(lines).strip("\n")
        if kind == "markdown":
            text = "\n".join(ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in text.splitlines())
            nb.cells.append(nbformat.v4.new_markdown_cell(text))
        elif kind == "code" and text.strip():
            nb.cells.append(nbformat.v4.new_code_cell(text))

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# %%"):
            flush()
            kind, lines = ("markdown" if "[markdown]" in line else "code"), []
        elif kind is not None:
            lines.append(line)
    flush()
    return nb


def build(name: str) -> None:
    src = SRC / f"{name}.py"
    nb = parse(src)
    started = time.perf_counter()
    NotebookClient(nb, timeout=900, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}}).execute()
    for cell in nb.cells:  # drop execution timing noise so rebuilds diff cleanly
        cell.get("metadata", {}).pop("execution", None)
    nbformat.write(nb, OUT / f"{name}.ipynb")
    print(f"built notebooks/{name}.ipynb in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(p.stem for p in SRC.glob("*.py"))
    for n in names:
        build(n)
