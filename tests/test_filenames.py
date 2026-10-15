"""Cruise IDs that aren't valid file names still give reports inside -o (issue 52)."""

import ast
import logging
from pathlib import Path

import nbformat
import pytest

from sbe_qa_processing import filenames
from sbe_qa_processing.cli import _cruise_toml_path, main
from sbe_qa_processing.filenames import report_stem, safe_filename


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    """report_stem warns once per identifier per process"""
    monkeypatch.setattr(filenames, "_warned", set())


@pytest.mark.parametrize(
    "name, expected",
    [
        ("SP2613", "SP2613"),
        ("SP2613_169847", "SP2613_169847"),
        ("BH18-18", "BH18-18"),
        ("AT50/12", "AT50_12"),
        ("../escaped", ".._escaped"),
        ("a\\b", "a_b"),
        ('T<S:1|"x"?*', "T_S_1__x___"),
        ("tab\there", "tab_here"),
        ("trailing. ", "trailing"),
        ("CON", "_CON"),
        ("con.toml", "_con.toml"),
        ("COM1", "_COM1"),
        ("CONTROL", "CONTROL"),
        ("..", "_"),
        ("", "_"),
        ("  ", "_"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_report_stem_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="sbe_qa_processing.filenames"):
        assert report_stem("SP2613") == "SP2613"
        assert report_stem("AT50/12") == "AT50_12"
        assert report_stem("AT50/12") == "AT50_12"
    assert [r.getMessage() for r in caplog.records] == [
        "'AT50/12' isn't a valid file name on every platform; the files are named AT50_12 instead"
    ]


def test_cruise_toml_path():
    assert _cruise_toml_path(Path("configs"), "AT50/12") == Path("configs") / "AT50_12.toml"
    assert _cruise_toml_path(Path("configs"), "NUL") == Path("configs") / "_NUL.toml"


@pytest.mark.parametrize(
    "cruise_id, stem",
    [("AT50/12", "AT50_12"), ("../escaped", ".._escaped"), ('T<S:1 "q" \\', "T_S_1 _q_ _")],
)
def test_run_writes_inside_output(cruise_id, stem, tmp_path):
    fileset = tmp_path / "casts"
    fileset.mkdir()
    config = tmp_path / "cruise.toml"
    # A TOML literal string keeps the ID exactly, backslashes and quotes included
    config.write_text(f"[cruise]\nid = '''{cruise_id}'''\n")
    output = tmp_path / "out"
    assert main(["run", str(fileset), "-c", str(config), "-o", str(output)]) == 0

    # Everything is directly in -o: no subdirectory from a /, nothing outside it from ..
    assert sorted(p.name for p in output.iterdir()) == sorted(
        [f"{stem}_qa_report.pdf", f"{stem}_plots", f"{stem}_qa.ipynb"]
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["casts", "cruise.toml", "out"]

    # The notebook shows the ID as given, and its code still parses with any characters in it
    notebook = nbformat.read(output / f"{stem}_qa.ipynb", as_version=4)
    assert cruise_id in notebook.cells[0].source
    for cell in notebook.cells:
        if cell.cell_type == "code":
            ast.parse("\n".join(line for line in cell.source.splitlines() if line[:1] != "%"))
    assert repr(f"{stem}_qa_report.pdf") in notebook.cells[-1].source
