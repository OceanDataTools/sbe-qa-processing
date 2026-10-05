from datetime import date

import pytest
from test_xmlcon import write_xmlcon

from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Thresholds
from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.hdr import Header
from sbe_qa_processing.loadout import (
    cast_loadout,
    instrument_settings,
    parse_calibration_date,
    sensor_name,
)
from sbe_qa_processing.xmlcon import parse_xmlcon


@pytest.mark.parametrize(
    "text, expected",
    [
        ("08-May-2025 ", date(2025, 5, 8)),
        ("06-Jan-26", date(2026, 1, 6)),
        ("01 Feb 2011", date(2011, 2, 1)),
        ("20 MAR2004", date(2004, 3, 20)),
        ("", None),
        ("unknown", None),
    ],
)
def test_parse_calibration_date(text, expected):
    assert parse_calibration_date(text) == expected


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("WET_LabsCStar", "Transmissometer (WET Labs C-Star)"),
        ("FluoroWetlabCDOM_Sensor", "CDOM fluorometer (WET Labs)"),
        ("SomeNewGadgetSensor", "Some New Gadget"),
    ],
)
def test_sensor_name(kind, expected):
    assert sensor_name(kind) == expected


def cast_data(tmp_path, secondary=True):
    config = parse_xmlcon(write_xmlcon(tmp_path, secondary=secondary))
    header = Header(values={"nmea utc (time)": "Jul 09 2026 18:04:25"})
    return CastData(cast=Cast("CAST1"), config=config, header=header)


def test_loadout_channels_and_calibration_age(tmp_path):
    rows = cast_loadout(cast_data(tmp_path))
    assert [r.channel for r in rows] == [
        "Primary temperature (F0)",
        "Primary conductivity (F1)",
        "Pressure (F2)",
        "Secondary temperature (F3)",
        "Secondary conductivity (F4)",
    ]
    temperature = rows[0]
    assert (temperature.sensor, temperature.serial_number) == ("Temperature", "4924")
    assert temperature.calibration_date == date(2025, 5, 8)
    assert temperature.age_days == (date(2026, 7, 9) - date(2025, 5, 8)).days
    # no calibration date in the test configuration
    assert rows[1].calibration_date is None and rows[1].age_days is None


def test_loadout_skips_unused_channels(tmp_path):
    rows = cast_loadout(cast_data(tmp_path, secondary=False))
    assert len(rows) == 3


def test_instrument_settings(tmp_path):
    text = instrument_settings(cast_data(tmp_path))
    assert "24 Hz" in text and "1 voltage words suppressed" in text
    assert "appended: NMEA position" in text


def test_calibration_age_check(tmp_path):
    import numpy as np

    from sbe_qa_processing.science import WARN, run_science_checks

    data = cast_data(tmp_path)
    n = 200
    data.time = np.arange(n) / 24
    data.pressure = np.linspace(0, 50, n)
    data.temperature = data.temperature2 = np.full(n, 10.0)
    data.conductivity = data.conductivity2 = np.full(n, 4.0)
    data.salinity = data.salinity2 = np.full(n, 35.0)
    checks = {c.name: c for c in run_science_checks(data, Thresholds())}
    age = checks["Sensor calibration age"]
    # the temperature sensors were calibrated 08-May-2025, 427 days before the cast
    assert age.status == WARN
    assert "Temperature 4924: 427 d" in age.detail
    relaxed = {
        c.name: c for c in run_science_checks(data, Thresholds(max_calibration_age_days=500))
    }
    assert relaxed["Sensor calibration age"].status == "pass"
