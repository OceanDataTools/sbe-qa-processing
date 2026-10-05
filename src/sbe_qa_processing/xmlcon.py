"""Parses Seasave .XMLCON instrument configurations for the SBE 911plus into seabirdscientific
calibration coefficients. seabirdscientific itself has no XMLCON reader.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import seabirdscientific.cal_coefficients as cc
from seabirdscientific.instrument_data import Sensors

# SBE 911plus channel layout: 5 frequency channels then 8 voltage channels (4 words of 2)
FREQUENCY_CHANNELS = ("temperature", "conductivity", "pressure", "temperature2", "conductivity2")
VOLTAGE_CHANNELS = 8


class XmlconError(ValueError):
    """The file isn't a readable SBE 911plus configuration"""


@dataclass
class SensorEntry:
    index: int
    kind: str  # element name, e.g. TemperatureSensor, ConductivitySensor, NotInUse
    serial_number: str
    calibration_date: str
    element: ET.Element = field(repr=False)

    @property
    def in_use(self) -> bool:
        return self.kind != "NotInUse"

    def number(self, tag: str, default: float | None = None) -> float:
        text = self.element.findtext(tag)
        if text is None or not text.strip():
            if default is None:
                raise XmlconError(f"{self.kind} {self.serial_number}: missing {tag}")
            return default
        return float(text)


@dataclass
class InstrumentConfig:
    name: str
    frequency_channels_suppressed: int
    voltage_words_suppressed: int
    scans_to_average: int
    nmea_position_added: bool
    nmea_depth_added: bool
    nmea_time_added: bool
    scan_time_added: bool
    surface_par_added: bool
    sensors: list[SensorEntry]

    @property
    def is_911(self) -> bool:
        return "911" in self.name

    @property
    def sample_interval(self) -> float:
        """[s] the 911plus scans at 24 Hz"""
        return self.scans_to_average / 24

    @property
    def frequency_sensors(self) -> dict[str, SensorEntry]:
        """Frequency channel name -> sensor; suppressed channels are absent"""
        count = len(FREQUENCY_CHANNELS) - self.frequency_channels_suppressed
        return dict(zip(FREQUENCY_CHANNELS[:count], self.sensors[:count], strict=False))

    @property
    def voltage_sensors(self) -> list[SensorEntry]:
        count = len(FREQUENCY_CHANNELS) - self.frequency_channels_suppressed
        return self.sensors[count:]

    def sensor(self, channel: str) -> SensorEntry | None:
        entry = self.frequency_sensors.get(channel)
        return entry if entry is not None and entry.in_use else None

    @property
    def has_secondary_pair(self) -> bool:
        return self.sensor("temperature2") is not None and self.sensor("conductivity2") is not None

    def enabled_sensors(self) -> list[Sensors]:
        """Sensors to pass to seabirdscientific's 911plus hex reader so every byte is consumed"""
        enabled = []
        if self.frequency_channels_suppressed <= 1:
            enabled.append(Sensors.SecondaryTemperature)
        if self.frequency_channels_suppressed == 0:
            enabled.append(Sensors.SecondaryConductivity)
        words = 4 - self.voltage_words_suppressed
        voltage_pairs = [
            (Sensors.ExtVolt0, Sensors.ExtVolt1),
            (Sensors.ExtVolt2, Sensors.ExtVolt3),
            (Sensors.ExtVolt4, Sensors.ExtVolt5),
            (Sensors.ExtVolt6, Sensors.ExtVolt7),
        ]
        for pair in voltage_pairs[:words]:
            enabled.extend(pair)
        if self.surface_par_added:
            enabled.append(Sensors.SPAR)
        if self.nmea_position_added:
            enabled.append(Sensors.nmeaLocation)
        if self.nmea_depth_added:
            enabled.append(Sensors.nmeaDepth)
        if self.nmea_time_added:
            enabled.append(Sensors.nmeaTime)
        if self.scan_time_added:
            enabled.append(Sensors.SystemTime)
        return enabled

    # Calibration coefficients ------------------------------------------------------------

    def temperature_coefficients(
        self, channel: str = "temperature"
    ) -> tuple[cc.TemperatureFrequencyCoefficients, float, float]:
        """Coefficients plus (slope, offset), which seabirdscientific doesn't apply"""
        entry = self._require(channel, "TemperatureSensor")
        coefs = cc.TemperatureFrequencyCoefficients(
            g=entry.number("G"),
            h=entry.number("H"),
            i=entry.number("I"),
            j=entry.number("J"),
            f0=entry.number("F0"),
        )
        return coefs, entry.number("Slope", 1.0), entry.number("Offset", 0.0)

    def conductivity_coefficients(
        self, channel: str = "conductivity"
    ) -> tuple[cc.ConductivityCoefficients, float, float]:
        """Coefficients (equation 1, G-J) plus (slope, offset)"""
        entry = self._require(channel, "ConductivitySensor")
        block = entry.element.find("Coefficients[@equation='1']")
        if block is None:
            raise XmlconError(f"conductivity {entry.serial_number}: no G-J coefficients")
        value = lambda tag, default=None: _number(block, tag, default)
        coefs = cc.ConductivityCoefficients(
            g=value("G"),
            h=value("H"),
            i=value("I"),
            j=value("J"),
            cpcor=value("CPcor"),
            ctcor=value("CTcor"),
            wbotc=value("WBOTC", 0.0),
        )
        return coefs, entry.number("Slope", 1.0), entry.number("Offset", 0.0)

    def pressure_coefficients(self) -> cc.PressureDigiquartzCoefficients:
        entry = self._require("pressure", "PressureSensor")
        return cc.PressureDigiquartzCoefficients(
            c1=entry.number("C1"),
            c2=entry.number("C2"),
            c3=entry.number("C3"),
            d1=entry.number("D1"),
            d2=entry.number("D2"),
            t1=entry.number("T1"),
            t2=entry.number("T2"),
            t3=entry.number("T3"),
            t4=entry.number("T4"),
            t5=entry.number("T5", 0.0),
            ad590m=entry.number("AD590M"),
            ad590b=entry.number("AD590B"),
            slope=entry.number("Slope", 1.0),
            offset=entry.number("Offset", 0.0),
        )

    def _require(self, channel: str, kind: str) -> SensorEntry:
        entry = self.sensor(channel)
        if entry is None or entry.kind != kind:
            raise XmlconError(f"no {kind} on the {channel} channel")
        return entry


def _number(element: ET.Element, tag: str, default: float | None = None) -> float:
    text = element.findtext(tag)
    if text is None or not text.strip():
        if default is None:
            raise XmlconError(f"missing {tag}")
        return default
    return float(text)


def _flag(instrument: ET.Element, tag: str) -> bool:
    return (instrument.findtext(tag) or "0").strip() == "1"


def parse_xmlcon(path: Path | str) -> InstrumentConfig:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise XmlconError(f"not valid XML: {error}") from error
    instrument = root.find("Instrument")
    if instrument is None:
        raise XmlconError("no <Instrument> element")
    name = (instrument.findtext("Name") or "").strip()
    if "911" not in name:
        raise XmlconError(
            f"configuration is for {name or 'an unnamed instrument'}, not an SBE 911plus"
        )
    sensors = []
    for sensor in instrument.iter("Sensor"):
        children = list(sensor)
        if not children:
            continue
        element = children[0]
        sensors.append(
            SensorEntry(
                index=int(sensor.get("index", len(sensors))),
                kind=element.tag,
                serial_number=(element.findtext("SerialNumber") or "").strip(),
                calibration_date=(element.findtext("CalibrationDate") or "").strip(),
                element=element,
            )
        )
    try:
        return InstrumentConfig(
            name=name,
            frequency_channels_suppressed=int(instrument.findtext("FrequencyChannelsSuppressed")),
            voltage_words_suppressed=int(instrument.findtext("VoltageWordsSuppressed")),
            scans_to_average=int(instrument.findtext("ScansToAverage") or 1),
            nmea_position_added=_flag(instrument, "NmeaPositionDataAdded"),
            nmea_depth_added=_flag(instrument, "NmeaDepthDataAdded"),
            nmea_time_added=_flag(instrument, "NmeaTimeAdded"),
            scan_time_added=_flag(instrument, "ScanTimeAdded"),
            surface_par_added=_flag(instrument, "SurfaceParVoltageAdded"),
            sensors=sorted(sensors, key=lambda s: s.index),
        )
    except (TypeError, ValueError) as error:
        raise XmlconError(f"incomplete instrument settings: {error}") from error
