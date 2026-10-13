"""Readable str() for the classes people inspect, and the same text in IPython (issue 36)."""

import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from IPython.lib.pretty import pretty

from sbe_qa_processing.bottles import BottleFire
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Extent, QAConfig, Thresholds, config_to_toml
from sbe_qa_processing.fileset import Cast, Fileset
from sbe_qa_processing.hdr import Header
from sbe_qa_processing.integrations.r2r.config import CruiseConfig
from sbe_qa_processing.integrations.r2r.config import config_to_toml as cruise_toml
from sbe_qa_processing.integrations.r2r.rules import Info, R2RResult, TestResult
from sbe_qa_processing.integrity import IntegrityResult
from sbe_qa_processing.qa import QAResult
from sbe_qa_processing.readable import counts
from sbe_qa_processing.science import CheckResult
from sbe_qa_processing.xmlcon import InstrumentConfig, SensorEntry

STARTED = datetime(2026, 7, 9, 18, 0, 0, tzinfo=UTC)


def cast(name="STN1", **files) -> Cast:
    paths = {kind: Path(f"/bag/data/{name}.{kind}") for kind in ("hex", "XMLCON", "hdr", "bl")}
    defaults = {
        "raw": paths["hex"],
        "config": paths["XMLCON"],
        "header": paths["hdr"],
        "bottles": paths["bl"],
    }
    return Cast(name, **(defaults | files))


def sensor(index, kind, serial="1234", calibrated="08-May-2025") -> SensorEntry:
    return SensorEntry(index, kind, serial, calibrated, ET.Element(kind))


def instrument() -> InstrumentConfig:
    sensors = [
        sensor(0, "TemperatureSensor", "4924"),
        sensor(1, "ConductivitySensor", "4537", "06-Jan-26"),
        sensor(2, "PressureSensor", "0381"),
        sensor(3, "NotInUse", "", ""),
        sensor(4, "NotInUse", "", ""),
        sensor(5, "OxygenSensor", "3357"),
    ]
    return InstrumentConfig(
        name="SBE 911plus/917plus CTD",
        frequency_channels_suppressed=0,
        voltage_words_suppressed=3,
        scans_to_average=1,
        nmea_position_added=True,
        nmea_depth_added=False,
        nmea_time_added=False,
        scan_time_added=False,
        surface_par_added=False,
        sensors=sensors,
    )


def cast_data(**fields) -> CastData:
    scans = 1440
    defaults = {
        "time": np.arange(scans) / 24,
        "pressure": np.linspace(-0.5, 502.1, scans),
        "temperature": np.full(scans, 15.0),
        "conductivity": np.full(scans, 4.0),
        "salinity": np.full(scans, 33.5),
        "pump": np.ones(scans, dtype=bool),
    }
    return CastData(cast("STN1"), **(defaults | fields))


def qa_result() -> QAResult:
    casts = [cast_data(), CastData(cast("DeckTest"), errors={"hex": "bad line 3"})]
    integrity = IntegrityResult(
        fileset=[
            CheckResult("Raw files complete", "pass", "2 of 2 casts pass"),
            CheckResult("Cast position", "warn", "1 of 2 casts pass", detail="DeckTest: none"),
        ],
        casts={"STN1": [CheckResult("Raw files complete", "pass", "all present")]},
    )
    science = {
        "STN1": [
            CheckResult("Dual temperature", "pass", "0.001 °C", "≤ 0.01 °C"),
            CheckResult("Lost scans", "fail", "1.2%", "≤ 0.1 %"),
        ]
    }
    return QAResult(
        config=QAConfig(cruise_id="SP2613"),
        fileset=Fileset(Path("/bag"), Path("/bag/data"), [c.cast for c in casts], is_bag=True),
        casts=casts,
        integrity=integrity,
        science=science,
        started=STARTED,
        finished=datetime(2026, 7, 9, 18, 0, 2, 500000, tzinfo=UTC),
        versions={"sbe-qa-processing": "0.1.0"},
    )


def test_check_result():
    assert str(CheckResult("Raw files complete", "pass", "all present")) == (
        "PASS  Raw files complete: all present"
    )
    assert str(
        CheckResult("Dual temperature", "warn", "0.004 °C", "≤ 0.002 °C", "below 20 dbar")
    ) == ("WARN  Dual temperature: 0.004 °C (limit ≤ 0.002 °C) - below 20 dbar")
    assert str(CheckResult("Bottles", "n/a", "no .bl file")) == "N/A   Bottles: no .bl file"


def test_integrity_result():
    integrity = qa_result().integrity
    assert str(integrity) == (
        "Fileset\n"
        "  PASS  Raw files complete: 2 of 2 casts pass\n"
        "  WARN  Cast position: 1 of 2 casts pass - DeckTest: none\n"
        "STN1\n"
        "  PASS  Raw files complete: all present"
    )


def test_cast_and_fileset():
    assert str(cast()) == "STN1: STN1.hex, STN1.XMLCON, STN1.hdr, STN1.bl"
    assert str(cast("DeckTest", header=None, bottles=None)) == (
        "DeckTest: DeckTest.hex, DeckTest.XMLCON (deck test; no header)"
    )
    fileset = Fileset(Path("/bag"), Path("/bag/data"), [cast(), cast("DeckTest")], is_bag=True)
    assert str(fileset) == (
        "BagIt bag /bag: 2 casts, 1 deck test\n"
        "  STN1: STN1.hex, STN1.XMLCON, STN1.hdr, STN1.bl\n"
        "  DeckTest: DeckTest.hex, DeckTest.XMLCON, DeckTest.hdr, DeckTest.bl (deck test)"
    )
    assert str(Fileset(Path("/casts"), Path("/casts"), [])) == "Directory /casts: 0 casts"


def test_cast_data():
    assert str(cast_data()) == (
        "STN1: 1440 scans, 1.0 min, -0.5 to 502.1 dbar\n"
        "  channels: pressure, temperature, conductivity, salinity, pump"
    )
    failed = CastData(cast("DeckTest"), errors={"hex": "bad line 3"})
    assert str(failed) == "DeckTest: not converted (deck test)\n  hex error: bad line 3"


def test_config_classes():
    assert (
        str(Extent(-117.38, -117.22, 32.59, 32.71)) == "32.59°N to 32.71°N, 117.38°W to 117.22°W"
    )
    assert str(Extent(178, -178, -10, 10)) == (
        "10°S to 10°N, 178°E to 178°W (across the antimeridian)"
    )
    lines = str(Thresholds(temperature_difference=0.02)).splitlines()
    assert lines[0] == "temperature_difference: 0.02 °C (default 0.01)"
    assert "temperature_range: -2.5 to 40 °C" in lines
    assert lines[-1] == "max_calibration_age_days: 365 days"
    config = QAConfig(cruise_id="SP2613", extent=Extent(-117.38, -117.22, 32.59, 32.71))
    assert str(config) == config_to_toml(config).rstrip("\n")
    cruise = CruiseConfig(
        cruise_id="SP2613", depart_date=date(2026, 7, 9), arrive_date=date(2026, 7, 9)
    )
    assert str(cruise) == cruise_toml(cruise).rstrip("\n")
    assert "[provenance]" in str(cruise)  # R2R's TOML, not the core config's


def test_xmlcon_classes():
    assert str(sensor(0, "TemperatureSensor", "4924")) == (
        "TemperatureSensor 4924, calibrated 08-May-2025"
    )
    assert str(sensor(3, "NotInUse")) == "not in use"
    assert str(instrument()) == (
        "SBE 911plus/917plus CTD; 24 Hz; 0 frequency channels and 3 voltage words suppressed; "
        "appended: NMEA position\n"
        "  temperature: TemperatureSensor 4924, calibrated 08-May-2025\n"
        "  conductivity: ConductivitySensor 4537, calibrated 06-Jan-26\n"
        "  pressure: PressureSensor 0381, calibrated 08-May-2025\n"
        "  temperature2: not in use\n"
        "  conductivity2: not in use\n"
        "  voltage 0: OxygenSensor 3357, calibrated 08-May-2025"
    )


def test_header():
    header = Header(
        values={
            "nmea latitude": "32 42.46 N",
            "nmea longitude": "117 14.22 W",
            "nmea utc (time)": "Jul 09 2026 14:45:09",
            "temperature sn": "4924",
            "conductivity sn": "4537",
        }
    )
    assert str(header) == (
        "cast time: 2026-07-09 14:45:09 UTC (NMEA)\n"
        "position: 32.7077°N, 117.237°W\n"
        "temperature SN 4924, conductivity SN 4537\n"
        "5 header values"
    )
    assert str(Header()) == "cast time: unknown\n0 header values"


def test_bottle_fire():
    fire = BottleFire(1, 3, STARTED, 6855, 6891, 202.6, 202.2, 203.0)
    assert str(fire) == (
        "bottle 3 (fire 1) at 2026-07-09 18:00:00 UTC, scans 6855-6891, 202.6 dbar (202.2 to 203.0)"
    )
    assert str(BottleFire(2, 4, None, 10, 46)) == "bottle 4 (fire 2) at unknown, scans 10-46"


def test_qa_result():
    assert str(qa_result()) == (
        "SP2613: 2 casts (1 converted, 1 deck test) in /bag\n"
        "  run 2026-07-09 18:00:00 UTC, 2.5 s; sbe-qa-processing 0.1.0\n"
        "  integrity checks: 1 pass, 1 warn\n"
        "    Cast position warn (1 of 2 casts pass): DeckTest: none\n"
        "  science checks: 1 pass, 1 fail\n"
        "    STN1: Lost scans fail (1.2%)"
    )


def test_r2r_classes():
    test = TestResult("Lat/Lon within NAV Ranges", "", "Y", [], result=75, passed=3, total=4)
    test.failures = ["STN4"]
    assert str(test) == "Y  Lat/Lon within NAV Ranges: 75% (3 of 4 passed)\n  failed: STN4"
    unitless = TestResult("Valid Checksum", "", "G", [], uom="Unitless", passed=2, total=2)
    assert str(unitless) == "G  Valid Checksum (2 of 2 passed)"
    assert str(Info("Casts without all Raw Files", "List", "")) == (
        "Casts without all Raw Files: none"
    )
    r2r = R2RResult("Y", [test], [Info("Total Raw Files", "Count", "4")], ["STN2.hex"])
    assert str(r2r) == (
        "R2R rating Y\n"
        "  Y  Lat/Lon within NAV Ranges: 75% (3 of 4 passed)\n"
        "    failed: STN4\n"
        "  Total Raw Files: 4\n"
        "  checksum failures: STN2.hex"
    )


def test_counts():
    assert counts(["info", "pass", "warn", "pass", "n/a"]) == "2 pass, 1 warn, 1 info, 1 n/a"
    assert counts([]) == "none"


@pytest.mark.parametrize(
    "value",
    [
        CheckResult("Raw files complete", "pass", "all present"),
        cast(),
        Extent(-117.38, -117.22, 32.59, 32.71),
        Thresholds(),
        QAConfig(cruise_id="SP2613"),
        cast_data(),
        instrument(),
        Header(),
        BottleFire(1, 3, STARTED, 6855, 6891),
        qa_result(),
        R2RResult("G", [], []),
    ],
    ids=lambda value: type(value).__name__,
)
def test_ipython_shows_str_and_repr_is_the_dataclasses(value):
    assert pretty(value) == str(value)
    assert repr(value).startswith(f"{type(value).__name__}(")
