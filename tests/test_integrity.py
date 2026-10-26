from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Extent, QAConfig
from sbe_qa_processing.fileset import Cast, Fileset, ManifestEntry, md5sum
from sbe_qa_processing.hdr import Header
from sbe_qa_processing.integrity import (
    area_check,
    bounding_extent,
    checksum_check,
    dates_check,
    nav_every_scan_check,
    order_check,
    position_check,
    raw_files_check,
    readable_check,
    run_integrity_checks,
    serial_check,
    summarize,
    time_check,
)
from sbe_qa_processing.science import FAIL, INFO, NA, PASS, WARN, CheckResult

NOW = datetime(2026, 10, 12, tzinfo=UTC)
FILE = Path("x")  # never opened


def header(**values) -> Header:
    return Header(values={key.lower(): value for key, value in values.items()})


def cast(name="Station1", raw=FILE, xmlcon=FILE, hdr=FILE, bottles=None, **fields) -> CastData:
    return CastData(cast=Cast(name, raw, xmlcon, hdr, bottles), **fields)


def at(time: str) -> Header:
    return header(**{"NMEA UTC (Time)": time})


def config(**fields) -> QAConfig:
    return QAConfig(
        cruise_id="TEST", depart_date=date(2026, 7, 9), arrive_date=date(2026, 7, 10), **fields
    )


def test_raw_files():
    assert raw_files_check(cast().cast).status == PASS
    check = raw_files_check(cast(hdr=None, xmlcon=None).cast)
    assert (check.status, check.value) == (FAIL, "missing .XMLCON/.con, .hdr")


def test_readable():
    assert readable_check(cast(pressure=np.zeros(3))).status == PASS
    assert readable_check(cast(raw=None)).status == NA
    check = readable_check(cast(errors={"hex": "no data scans"}))
    assert (check.status, check.value, check.detail) == (
        FAIL,
        "failed: .hex/.dat",
        "no data scans",
    )


@pytest.mark.parametrize(
    "data, status",
    [
        (
            cast(header=header(**{"NMEA Latitude": "32 36.25 N", "NMEA Longitude": "117 22 W"})),
            PASS,
        ),
        # the first valid appended position, past blank scans
        (cast(latitude=np.array([0.0, np.nan, 32.6]), longitude=np.array([0.0, 0, -117])), PASS),
        (cast(header=header(**{"NMEA Latitude": "0 0 N", "NMEA Longitude": "0 0 E"})), FAIL),
        (cast(latitude=np.array([0.0]), longitude=np.array([0.0])), FAIL),
        (cast(), FAIL),
    ],
)
def test_position(data, status):
    assert position_check(data).status == status


def test_nav_on_every_scan():
    assert nav_every_scan_check(cast()).status == NA
    data = cast(latitude=np.array([32.6, np.nan]), longitude=np.array([-117.0, -117.0]))
    check = nav_every_scan_check(data)
    assert (check.status, check.value) == (WARN, "1 scans without position")


def test_area_across_the_antimeridian():
    across = config(extent=Extent(178.0, -178.0, -20.0, -15.0))

    def at_position(latitude, longitude, name="Station1"):
        return cast(name, latitude=np.array([latitude]), longitude=np.array([longitude]))

    assert area_check(at_position(-17.0, 179.5), across).status == PASS
    assert area_check(at_position(-17.0, -179.2), across).status == PASS
    assert area_check(at_position(-17.0, 170.0), across).status == FAIL
    assert area_check(at_position(-17.0, 170.0, "DeckTest"), across).status == NA
    assert (
        area_check(at_position(-17.0, 179.5), config()).value == "no expected area in the config"
    )
    assert area_check(cast(), across).status == NA


@pytest.mark.parametrize(
    "data, status, detail",
    [
        (cast(header=at("Jul 09 2026 18:00:00")), PASS, "NMEA time"),
        (
            cast(header=header(**{"System UTC": "Jul 09 2026 18:00:00"})),
            PASS,
            "deck unit clock (no NMEA time)",
        ),
        (cast(header=at("Jan 01 1980 00:00:00")), FAIL, "before 1990: an unset clock?"),
        (cast(header=at("Jan 01 2027 00:00:00")), FAIL, "in the future"),
        (cast(), FAIL, "no NMEA or system time in the header"),
    ],
)
def test_cast_time(data, status, detail):
    check = time_check(data, NOW)
    assert (check.status, check.detail) == (status, detail)


def test_expected_dates():
    assert dates_check(cast(header=at("Jul 10 2026 23:59:59")), config()).status == PASS
    assert dates_check(cast(header=at("Jul 11 2026 00:00:01")), config()).status == FAIL
    assert dates_check(cast("DockTest", header=at("Jul 01 2026 12:00:00")), config()).status == NA
    assert dates_check(cast(), config()).status == NA
    undated = config()
    undated.depart_date = undated.arrive_date = None
    assert dates_check(cast(header=at("Jul 10 2026 12:00:00")), undated).status == NA


def instrument(temperature="4924", conductivity="3470"):
    serials = {"temperature": temperature, "conductivity": conductivity}
    return SimpleNamespace(
        sensor=lambda channel: (
            SimpleNamespace(serial_number=serials[channel]) if serials.get(channel) else None
        )
    )


@pytest.mark.parametrize(
    "hdr, configured, status",
    [
        (header(**{"Temperature SN": "04924", "Conductivity SN": "3470"}), instrument(), PASS),
        (header(**{"Temperature SN": "4925", "Conductivity SN": "3470"}), instrument(), FAIL),
        (header(**{"Temperature SN": "4924"}), instrument(), WARN),
        (header(), instrument(None, None), NA),
        (None, instrument(), NA),
    ],
)
def test_serial_numbers(hdr, configured, status):
    assert serial_check(cast(header=hdr, config=configured)).status == status


def test_checksums(tmp_path):
    good, bad = tmp_path / "a.hex", tmp_path / "b.hex"
    good.write_text("scans")
    bad.write_text("changed")
    manifest = [
        ManifestEntry("a.hex", md5sum(good)),
        ManifestEntry("b.hex", "0" * 32),
        ManifestEntry("missing.hex", "0" * 32),
    ]
    fileset = Fileset(tmp_path, tmp_path, [], manifest=manifest)
    check = checksum_check(fileset)
    assert (check.status, check.value) == (FAIL, "1 of 3 files")
    assert "b.hex, missing.hex" in check.detail
    assert checksum_check(Fileset(tmp_path, tmp_path, [])).status == NA
    # files changed after the manifest was written aren't checked yet
    fileset.manifest = manifest[:2]
    fileset.manifest_mtime = 0
    check = checksum_check(fileset)
    assert (check.status, check.value) == (PASS, "0 of 0 files")
    assert "2 changed after the manifest was written" in check.detail


def test_time_order():
    casts = [
        cast("Station10", header=at("Jul 09 2026 15:00:00")),
        cast("Station2", header=at("Jul 09 2026 12:00:00")),
        cast("Station1", header=at("Jul 09 2026 10:00:00")),
        # deck tests aren't part of the station sequence
        cast("DeckTest", header=at("Jul 10 2026 10:00:00")),
    ]
    assert order_check(casts).status == PASS
    casts.append(cast("Station11", header=at("Jul 09 2026 11:00:00")))
    check = order_check(casts)
    assert (check.status, check.detail) == (WARN, "Station11")
    assert order_check(casts[:1]).status == NA


def test_summary():
    results = {
        "Station1": CheckResult("c", PASS, "ok", "limit"),
        "Station2": CheckResult("c", FAIL, "bad", "limit"),
        "DeckTest": CheckResult("c", NA, "deck test"),
    }
    check = summarize("c", results)
    assert (check.status, check.value, check.limit, check.detail) == (
        FAIL,
        "1 of 2 casts pass",
        "limit",
        "Station2: bad",
    )
    assert summarize("c", {"DeckTest": results["DeckTest"]}).value == "deck test"


def test_run_integrity_checks(tmp_path):
    casts = [
        cast("Station1", header=at("Jul 09 2026 10:00:00")),
        cast("DeckTest", hdr=None),
    ]
    fileset = Fileset(tmp_path, tmp_path, [c.cast for c in casts])
    result = run_integrity_checks(fileset, casts, config(), NOW)
    assert set(result.casts) == {"Station1", "DeckTest"}
    by_name = {c.name: c for c in result.fileset}
    assert by_name["Raw files complete"].detail == "DeckTest: missing .hdr"
    assert by_name["Checksums match the manifest"].status == NA
    assert by_name["Deck-test casts"].detail == "DeckTest"


@pytest.mark.parametrize(
    "positions, expected",
    [
        ([(32.6, -117.3)], Extent(-117.3, -117.3, 32.6, 32.6)),
        ([(32.6, -117.3), (33.0, -117.2)], Extent(-117.3, -117.2, 32.6, 33.0)),
        # across the antimeridian, the smaller box
        ([(-17.0, 179.5), (-17.5, -179.2)], Extent(179.5, -179.2, -17.5, -17.0)),
        ([], None),
    ],
)
def test_bounding_extent(positions, expected):
    assert bounding_extent(positions) == expected


def test_coverage_without_a_config(tmp_path):
    casts = [
        cast(
            "Station1",
            header=at("Jul 09 2026 10:00:00"),
            latitude=np.array([32.6]),
            longitude=np.array([-117.3]),
        ),
        cast(
            "Station2",
            header=at("Jul 10 2026 11:30:00"),
            latitude=np.array([33.0]),
            longitude=np.array([-117.2]),
        ),
        # left out: a deck test, and a position and time that aren't plausible
        cast("DeckTest", header=at("Jul 01 2026 10:00:00")),
        cast(
            "Station3",
            header=at("Jan 01 1980 00:00:00"),
            latitude=np.array([0.0]),
            longitude=np.array([0.0]),
        ),
    ]
    fileset = Fileset(tmp_path, tmp_path, [c.cast for c in casts])
    result = run_integrity_checks(fileset, casts, QAConfig(), NOW)
    by_name = {c.name: c for c in result.fileset}
    assert by_name["Cast times"].value == "2026-07-09 10:00 to 2026-07-10 11:30 UTC"
    assert by_name["Cast positions"].value == "-117.3 to -117.2 E, 32.6 to 33 N"
    assert by_name["Position inside the expected area"].value == "no expected area in the config"
    assert (
        by_name["Cast time inside the expected dates"].value == "no expected dates in the config"
    )


def test_no_casts_warns(tmp_path):
    result = run_integrity_checks(Fileset(tmp_path, tmp_path, []), [], QAConfig(), NOW)
    casts = next(c for c in result.fileset if c.name == "Casts")
    assert (casts.status, casts.value, casts.detail) == (
        WARN,
        "0",
        f"no .hex, .dat or .hdr files in {tmp_path}",
    )
    one = cast("Station1", header=at("Jul 09 2026 10:00:00"))
    result = run_integrity_checks(Fileset(tmp_path, tmp_path, [one.cast]), [one], config(), NOW)
    assert next(c for c in result.fileset if c.name == "Casts").status == INFO
