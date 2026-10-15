"""Regressions for the code review findings (issues 39-48)."""

import os
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

    # Windows doesn't allow < or > in file names, and the directory name and the cruise ID name
    # files; the cruise name and PI still test the escaping there
    windows = os.name == "nt"
    fileset = tmp_path / ("A&B C" if windows else "A<B & C>")
    fileset.mkdir()
    cruise_id = "T&S" if windows else "T<S"
    config = QAConfig(cruise_id=cruise_id, cruise_name="R/V <Atlantis> & friends", cruise_pi="A<B")
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


def test_same_named_casts_in_subdirectories(tmp_path):
    """Issue 44"""
    from sbe_qa_processing.fileset import load_fileset

    for leg in ("leg1", "leg2"):
        (tmp_path / leg).mkdir()
        for suffix in (".hex", ".XMLCON", ".hdr"):
            (tmp_path / leg / f"CTD001{suffix}").write_text("")
    (tmp_path / "CTD002.hex").write_text("")
    names = [c.name for c in load_fileset(tmp_path).casts]
    assert names == ["CTD002", "leg1/CTD001", "leg2/CTD001"]
    # The deck test pattern looks at the file name only
    assert not Cast("tests/CTD001").is_deck_test and Cast("leg1/DeckTest").is_deck_test


def test_r2r_xml_without_a_manifest(tmp_path):
    """Issue 46"""

    from conftest import CONFIGS

    from sbe_qa_processing.integrations.r2r.catalog import R2R_NAMESPACE
    from sbe_qa_processing.integrations.r2r.reports import run_r2r
    from sbe_qa_processing.integrations.r2r.xml_report import build_qa_xml

    fileset = tmp_path / "PLAIN"
    fileset.mkdir()
    run = run_r2r(fileset, CONFIGS / "SP2613.toml")
    checksums = {t.name: t for t in run.r2r.tests}["Valid Checksum for All Files in Manifest"]
    assert (checksums.rating, checksums.failures) == ("X", ["no manifest-md5.txt"])
    assert run.r2r.checksum_failures == []
    log = [
        e.text
        for e in build_qa_xml(run.result, run.r2r).getroot().iter(f"{{{R2R_NAMESPACE}}}entry")
    ]
    assert log == ["no checksum manifest: checksums not verified"]


def test_fetch_fileset_only_follows_links_under_the_listing(tmp_path, monkeypatch):
    """Issue 47"""
    from sbe_qa_processing.integrations.r2r import download

    base = "https://ncei.example/bag/"
    pages = {
        base: '<a href="../">Parent</a> <a href="http://elsewhere/x">x</a> '
        '<a href="a%2F..%2Fb">sneaky</a> <a href="bagit.txt">bagit.txt</a> '
        '<a href="data/">data/</a> <a href="?C=N;O=D">sort</a>',
        base + "data/": '<a href="../">Parent</a> <a href="CAST1.hex">CAST1.hex</a>',
    }
    fetched = []

    def fetch(opener, url):
        fetched.append(url)
        return url, pages.get(url, "contents of " + url).encode()

    monkeypatch.setattr(download, "fetch", fetch)
    assert download.download_tree(None, base, tmp_path / "bag") == 2
    assert sorted(
        p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()
    ) == [
        "bag/bagit.txt",
        "bag/data/CAST1.hex",
    ]
    assert all(url.startswith(base) for url in fetched)


def test_header_reading_stops_at_end():
    """Issue 48: a .hex's data after *END* isn't read"""
    from sbe_qa_processing.hdr import parse_header, parse_header_lines

    read = []

    def lines():
        for line in ["* Temperature SN = 4924\n", "*END*\n", "0A1B2C\n", "0A1B2D\n"]:
            read.append(line)
            yield line

    header = parse_header_lines(lines())
    assert header.temperature_serial == "4924" and len(read) == 2
    # The same header from a real .hex as from reading the whole file, when it's downloaded
    for path in sorted(Path("data/SP2613_169847_ctd/data").glob("*.hex"))[:1]:
        whole = parse_header_lines(path.read_text(encoding="latin-1").splitlines(keepends=True))
        assert parse_header(path) == whole
