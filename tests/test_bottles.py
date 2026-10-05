from datetime import UTC, datetime

import numpy as np
import pytest

from sbe_qa_processing.bottles import bottle_fires, parse_bottle_log
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.fileset import Cast

LOG = """E:\\CTD\\SP2613\\SP2613_Station4.bl
RESET Jul 09 2026 20:55:03
1, 1, Jul 09 2026 20:59:52, 100, 136
2, 3, Jul 09 2026 21:04:05, 300, 336
not a fire line
"""


@pytest.fixture
def bottle_log(tmp_path):
    path = tmp_path / "CAST.bl"
    path.write_text(LOG)
    return path


def test_parse_bottle_log(bottle_log):
    fires = parse_bottle_log(bottle_log)
    assert [(f.sequence, f.position) for f in fires] == [(1, 1), (2, 3)]
    assert fires[0].time == datetime(2026, 7, 9, 20, 59, 52, tzinfo=UTC)
    assert (fires[1].first_scan, fires[1].last_scan) == (300, 336)


def test_pressure_is_mean_over_the_fires_scans(bottle_log):
    pressure = np.arange(500, dtype=float)  # pressure equals scan index
    data = CastData(cast=Cast("CAST", bottles=bottle_log), pressure=pressure)
    first, second = bottle_fires(data)
    assert first.pressure == pytest.approx(118.0)  # mean of scans 100..136
    assert (first.pressure_min, first.pressure_max) == (100, 136)
    assert second.pressure == pytest.approx(318.0)


def test_no_bottle_log_or_no_fires(tmp_path):
    assert bottle_fires(CastData(cast=Cast("CAST"))) == []
    empty = tmp_path / "EMPTY.bl"
    empty.write_text("E:\\CTD\\EMPTY.bl\nRESET Jul 09 2026 18:04:24\n")
    assert bottle_fires(CastData(cast=Cast("EMPTY", bottles=empty))) == []


def test_pdf_table_only_when_bottles_fired(bottle_log, tmp_path):
    from sbe_qa_processing.pdf_report import _bottle_table

    data = CastData(cast=Cast("CAST", bottles=bottle_log), pressure=np.arange(500, dtype=float))
    assert _bottle_table(data) is not None
    empty = tmp_path / "EMPTY.bl"
    empty.write_text("E:\\CTD\\EMPTY.bl\nRESET Jul 09 2026 18:04:24\n")
    assert _bottle_table(CastData(cast=Cast("EMPTY", bottles=empty))) is None


def rosette_log(tmp_path, bottles: int):
    lines = ["E:\\CTD\\CAST.bl", "RESET Jul 09 2026 20:55:03"]
    for n in range(1, bottles + 1):
        lines.append(f"{n}, {n}, Jul 09 2026 21:{n % 60:02d}:00, {100 * n}, {100 * n + 36}")
    path = tmp_path / f"CAST{bottles}.bl"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.parametrize("bottles, chart_on_same_page", [(12, True), (24, True), (60, False)])
def test_bottle_page_fits_typical_rosettes(tmp_path, bottles, chart_on_same_page):
    import matplotlib.pyplot as plt
    from reportlab.platypus import PageBreak

    from sbe_qa_processing.pdf_report import _bottle_page

    figure = plt.figure(figsize=(7.5, 4.3))
    figure.add_subplot().plot([0, 1], [0, 1])
    svg = tmp_path / "pressure.svg"
    figure.savefig(svg, format="svg")
    plt.close(figure)

    data = CastData(
        cast=Cast("CAST", bottles=rosette_log(tmp_path, bottles)),
        pressure=np.linspace(0, 2000, 100 * bottles + 100),
    )
    flowables = _bottle_page(data, {"CAST/pressure": svg})
    # 12-24 bottles: table and pressure record share the page; very large rosettes push the
    # plot to the next page rather than shrinking it
    assert any(isinstance(f, PageBreak) for f in flowables) is not chart_on_same_page
