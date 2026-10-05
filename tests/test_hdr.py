from datetime import UTC, datetime

import pytest

from sbe_qa_processing.hdr import parse_coordinate, parse_header_lines, parse_time

HEADER = """* Sea-Bird SBE 9 Data File:
* Temperature SN = 4924
* Conductivity SN = 4537
* System UpLoad Time = Jul 09 2026 18:04:26
* NMEA Latitude = 32 36.25 N
* NMEA Longitude = 117 22.04 W
* NMEA UTC (Time) = Jul 09 2026  18:04:25
* Store Lat/Lon Data = Append to Every Scan
** Ship: Sproul SP2613
* System UTC = Jul 09 2026 18:04:26
*END*
""".splitlines()


@pytest.mark.parametrize(
    "text, expected",
    [("32 36.25 N", 32.604167), ("117 22.04 W", -117.367333), ("0 30.00 S", -0.5)],
)
def test_parse_coordinate(text, expected):
    assert parse_coordinate(text) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("text", [None, "", "32.6 N", "32 61.00 N", "garbage"])
def test_parse_coordinate_rejects_unreadable(text):
    assert parse_coordinate(text) is None


def test_parse_time_tolerates_double_spaces():
    assert parse_time("Jul 09 2026  18:04:25") == datetime(2026, 7, 9, 18, 4, 25, tzinfo=UTC)


def test_parse_header():
    header = parse_header_lines(HEADER)
    assert header.temperature_serial == "4924"
    assert header.conductivity_serial == "4537"
    assert header.latitude == pytest.approx(32.604167, abs=1e-6)
    assert header.longitude == pytest.approx(-117.367333, abs=1e-6)
    assert header.cast_time == datetime(2026, 7, 9, 18, 4, 25, tzinfo=UTC)
    assert header.lat_lon_appended
