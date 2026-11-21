"""Build the Colab notebook from train_tomato.py (standard library only).

The .py file is the source of truth (reviewable diffs); cells are separated by
`# %%` lines and `# %% [markdown]` cells hold Markdown in comment lines.

    python build_notebook.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "train_tomato.py"
TARGET = HERE / "train_tomato_colab.ipynb"


def build() -> dict:
    cells, kind, lines = [], None, []

    def flush() -> None:
        if kind is None:
            return
        text = "\n".join(lines).strip("\n")
        if kind == "markdown":
            text = "\n".join(line[2:] if line.startswith("# ") else line.lstrip("#") for line in text.splitlines())
        source = [line + "\n" for line in text.splitlines()]
        if source:
            source[-1] = source[-1].rstrip("\n")
        cell = {"cell_type": kind, "metadata": {}, "source": source}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if line.startswith("# %%"):
            flush()
            kind, lines = ("markdown" if "[markdown]" in line else "code"), []
        else:
            lines.append(line)
    flush()

    return {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


if __name__ == "__main__":
    TARGET.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET.name}")
