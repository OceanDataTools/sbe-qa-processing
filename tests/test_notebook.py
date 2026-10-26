"""The notebook that re-runs the assessment, written from notebook_template.py and executed on
the committed SP2613 fileset, which has casts with bottle fires.
"""

import re

import nbformat
import pytest
from conftest import CONFIGS, r2r_fileset

from sbe_qa_processing.integrations.r2r.reports import run_r2r, write_r2r_reports
from sbe_qa_processing.notebook import TEMPLATE, build_notebook
from sbe_qa_processing.qa import run_qa

pytest.importorskip("nbclient")


def errors(notebook) -> list:
    return [
        output
        for cell in notebook.cells
        if cell.cell_type == "code"
        for output in cell.outputs
        if output.output_type == "error"
    ]


def printed(notebook) -> str:
    return "".join(
        output.get("text", "")
        for cell in notebook.cells
        if cell.cell_type == "code"
        for output in cell.outputs
        if output.output_type == "stream" and output.get("name") == "stdout"
    )


def test_every_marker_is_filled(tmp_path):
    notebook = build_notebook(run_qa(r2r_fileset("SP2613_169847_ctd")), tmp_path)
    text = "\n".join(cell.source for cell in notebook.cells)
    assert "# @" not in text and "# %%" not in text
    assert not re.search(r"\{(cruise_id|heading|cruise_name|reports|checks|version)\}", text)
    markers = set(re.findall(r"# @(\w+)$", TEMPLATE.read_text(), re.MULTILINE))
    assert markers == {
        "config_path",
        "fileset",
        "theme_file",
        "output_dir",
        "extras_imports",
        "run",
        "after_run",
        "cells",
        "write",
        "write_pdf",
    }


def test_the_r2r_notebook_reruns_the_assessment(tmp_path):
    """R2R's notebook runs every core cell and R2R's; test_review_fixes runs the core one
    without a config or casts
    """
    run = run_r2r(r2r_fileset("SP2613_169847_ctd"), CONFIGS / "SP2613.toml")
    write_r2r_reports(run, tmp_path, execute_notebook=True)
    notebook = nbformat.read(tmp_path / "SP2613_169847_qa.ipynb", as_version=4)
    assert not errors(notebook), errors(notebook)
    assert f"R2R rating {run.r2r.rating}" in printed(notebook)
    assert re.search(r"\d+ bottles", printed(notebook))  # the bottle fires cell ran
