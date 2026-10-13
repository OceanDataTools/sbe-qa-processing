"""Regressions for the code review findings (issues 39-48)."""

from pathlib import Path

import numpy as np

from sbe_qa_processing import qa
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.fileset import Cast, Fileset

FILE = Path("x")  # never opened


def test_conductivity_failure_doesnt_stop_the_run(tmp_path, monkeypatch):
    """Issue 39"""
    cast = Cast("Station1", FILE, FILE, FILE)

    def read_cast(c):
        n = 100
        return CastData(
            cast=c,
            time=np.arange(n) / 24,
            pressure=np.linspace(0, 50, n),
            temperature=np.full(n, 10.0),
            errors={"conversion": "conductivity: no G-J coefficients"},
        )

    monkeypatch.setattr(qa, "read_cast", read_cast)
    result = qa.run_qa(Fileset(tmp_path, tmp_path, [cast]))
    (data,) = result.casts
    assert not data.converted and result.science == {}
    readable = {c.name: c for c in result.integrity.casts["Station1"]}["Files readable"]
    assert (readable.status, readable.value) == ("fail", "failed: conversion")


def test_ts_diagram_without_valid_salinity():
    """Issue 40"""
    import matplotlib.pyplot as plt

    from sbe_qa_processing import plots

    n = 200
    data = CastData(
        cast=Cast("Station1"),
        time=np.arange(n) / 24,
        pressure=np.concatenate([np.linspace(0, 50, n // 2), np.linspace(50, 0, n // 2)]),
        temperature=np.full(n, 10.0),
        salinity=np.zeros(n),  # a dead conductivity cell
    )
    figure = plots.ts_diagram(data)
    assert any("no salinity above" in t.get_text() for t in figure.axes[0].texts)
    plt.close(figure)


def test_markup_characters_in_report_text(tmp_path):
    """Issue 41"""
    from sbe_qa_processing.config import QAConfig
    from sbe_qa_processing.pdf_report import write_pdf

    fileset = tmp_path / "A<B & C>"
    fileset.mkdir()
    config = QAConfig(cruise_id="T<S", cruise_name="R/V <Atlantis> & friends", cruise_pi="A<B")
    result = qa.run_qa(fileset, config)
    pdf = write_pdf(result, tmp_path / "report.pdf")
    assert pdf.stat().st_size > 0


def test_notebook_runs_without_casts(tmp_path):
    """Issue 42: the generated notebook executes for a fileset with no casts"""
    import pytest

    pytest.importorskip("nbclient")
    from sbe_qa_processing.notebook import write_notebook

    fileset = tmp_path / "EMPTY"
    fileset.mkdir()
    result = qa.run_qa(fileset)
    path = write_notebook(result, tmp_path / "EMPTY_qa.ipynb", tmp_path, execute=True)
    text = path.read_text()
    assert "no casts" in text and "no science checks" in text


def test_map_ignores_blank_positions(monkeypatch):
    """Issue 43"""
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt

    from sbe_qa_processing import maps, plots
    from sbe_qa_processing.config import QAConfig
    from sbe_qa_processing.hdr import Header

    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))

    def at(name, latitude, longitude):
        values = {"nmea latitude": latitude, "nmea longitude": longitude}
        return CastData(cast=Cast(name), header=Header(values=values))

    casts = [at("Station1", "32 36.25 N", "117 22.04 W"), at("Blank", "00 00.00 N", "000 00.00 E")]
    figure = plots.cast_map(casts, QAConfig())
    west, east, south, north = figure.axes[0].get_extent(crs=ccrs.PlateCarree())
    assert -118 < west < east < -116 and 32 < south < north < 33.5
    plt.close(figure)
