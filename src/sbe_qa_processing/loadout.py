"""Sensor loadout of a cast: which sensor is on each SBE 911plus channel, with serial numbers
and calibration dates from the cast's .XMLCON.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sbe_qa_processing.cast import CastData

FREQUENCY_LABELS = {
    "temperature": "Primary temperature (F0)",
    "conductivity": "Primary conductivity (F1)",
    "pressure": "Pressure (F2)",
    "temperature2": "Secondary temperature (F3)",
    "conductivity2": "Secondary conductivity (F4)",
}

# Seasave's XMLCON element names for common sensors
SENSOR_NAMES = {
    "TemperatureSensor": "Temperature",
    "ConductivitySensor": "Conductivity",
    "PressureSensor": "Pressure (Digiquartz)",
    "OxygenSensor": "Oxygen (SBE 43)",
    "PAR_BiosphericalLicorChelseaSensor": "PAR (Biospherical/Licor/Chelsea)",
    "SPAR_Sensor": "Surface PAR",
    "WET_LabsCStar": "Transmissometer (WET Labs C-Star)",
    "FluoroWetlabECO_AFL_FL_Sensor": "Fluorometer (WET Labs ECO-AFL/FL)",
    "FluoroWetlabWetstarSensor": "Fluorometer (WET Labs WETStar)",
    "FluoroWetlabCDOM_Sensor": "CDOM fluorometer (WET Labs)",
    "OxidationReductionPotentialSensor": "Oxidation-reduction potential (ORP)",
    "AltimeterSensor": "Altimeter",
    "pH_Sensor": "pH",
    "UserPolynomialSensor": "User polynomial",
}

_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d %b%Y", "%Y-%m-%d", "%m/%d/%Y")


@dataclass
class SensorRow:
    channel: str
    sensor: str
    serial_number: str
    calibration_date: date | None
    calibration_text: str
    age_days: int | None  # calibration age at the time of the cast


def sensor_name(kind: str) -> str:
    """A readable name for an XMLCON sensor element, e.g. WET_LabsCStar"""
    if kind in SENSOR_NAMES:
        return SENSOR_NAMES[kind]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", kind.replace("_", " "))
    return re.sub(r"\s*Sensor$", "", words).strip()


def parse_calibration_date(text: str) -> date | None:
    text = " ".join(text.split())
    for fmt in _DATE_FORMATS:
        try:
            # Calibration dates are calendar dates; UTC only satisfies the parser
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def cast_loadout(data: CastData) -> list[SensorRow]:
    """The sensors in use on a cast, in channel order (empty without a readable XMLCON)"""
    config = data.config
    if config is None:
        return []
    cast_date = data.header.cast_time.date() if data.header and data.header.cast_time else None
    channels = [
        (FREQUENCY_LABELS[name], entry) for name, entry in config.frequency_sensors.items()
    ]
    channels += [(f"Voltage {i}", entry) for i, entry in enumerate(config.voltage_sensors)]
    rows = []
    for channel, entry in channels:
        if not entry.in_use:
            continue
        calibrated = parse_calibration_date(entry.calibration_date)
        rows.append(
            SensorRow(
                channel=channel,
                sensor=sensor_name(entry.kind),
                serial_number=entry.serial_number,
                calibration_date=calibrated,
                calibration_text=entry.calibration_date,
                age_days=(cast_date - calibrated).days if calibrated and cast_date else None,
            )
        )
    return rows


def instrument_settings(data: CastData) -> str:
    """One line describing the deck unit settings, e.g. what is appended to each scan"""
    config = data.config
    if config is None:
        return ""
    appended = [
        label
        for flag, label in (
            (config.nmea_position_added, "NMEA position"),
            (config.nmea_depth_added, "NMEA depth"),
            (config.nmea_time_added, "NMEA time"),
            (config.scan_time_added, "scan time"),
            (config.surface_par_added, "surface PAR"),
        )
        if flag
    ]
    parts = [
        config.name,
        f"{24 / config.scans_to_average:g} Hz",
        (
            f"{config.frequency_channels_suppressed} frequency channels and "
            f"{config.voltage_words_suppressed} voltage words suppressed"
        ),
    ]
    if appended:
        parts.append("appended: " + ", ".join(appended))
    return "; ".join(parts)
