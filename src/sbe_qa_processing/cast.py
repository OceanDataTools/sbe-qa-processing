"""Reads one SBE 911plus cast and converts it to engineering units with seabirdscientific."""

from dataclasses import dataclass, field

import gsw
import numpy as np
from seabirdscientific import conversion
from seabirdscientific import instrument_data as idata

from sbe_qa_processing import status
from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.hdr import Header, parse_header
from sbe_qa_processing.xmlcon import InstrumentConfig, XmlconError, parse_xmlcon


@dataclass
class CastData:
    """Converted channels for one cast. Arrays are per scan; absent channels are None."""

    cast: Cast
    config: InstrumentConfig | None = None
    header: Header | None = None
    errors: dict[str, str] = field(default_factory=dict)  # "xmlcon" / "hex" / "conversion"
    time: np.ndarray | None = None  # [s] since the first scan
    pressure: np.ndarray | None = None  # [dbar]
    temperature: np.ndarray | None = None  # [ITS-90 deg C] primary
    conductivity: np.ndarray | None = None  # [S/m] primary
    salinity: np.ndarray | None = None  # [PSU] primary
    temperature2: np.ndarray | None = None  # secondary pair, when fitted
    conductivity2: np.ndarray | None = None
    salinity2: np.ndarray | None = None
    latitude: np.ndarray | None = None  # NMEA position appended to each scan
    longitude: np.ndarray | None = None
    pump: np.ndarray | None = None  # True when the pump is on
    bottle_confirm: np.ndarray | None = None  # True while the deck unit sees a bottle fire
    modulo: np.ndarray | None = None  # 8-bit scan counter from the deck unit

    @property
    def name(self) -> str:
        return self.cast.name

    @property
    def scans(self) -> int:
        return 0 if self.pressure is None else len(self.pressure)

    @property
    def converted(self) -> bool:
        return self.pressure is not None and self.temperature is not None

    @property
    def has_secondary(self) -> bool:
        return self.temperature2 is not None and self.conductivity2 is not None


def _temperature(frequency, config: InstrumentConfig, channel: str) -> np.ndarray:
    coefs, slope, offset = config.temperature_coefficients(channel)
    return slope * conversion.convert_temperature_frequency(frequency, coefs) + offset


def _conductivity(frequency, temperature, pressure, config, channel) -> np.ndarray:
    coefs, slope, offset = config.conductivity_coefficients(channel)
    raw = conversion.convert_conductivity(
        frequency, temperature, pressure, coefs, idata.InstrumentType.SBE911Plus
    )
    return slope * raw + offset


def _salinity(conductivity, temperature, pressure) -> np.ndarray:
    # gsw takes conductivity in mS/cm
    return gsw.SP_from_C(conductivity * 10, temperature, pressure)


def read_cast(cast: Cast) -> CastData:
    data = CastData(cast=cast)
    if cast.header is not None:
        data.header = parse_header(cast.header)
    if cast.config is None:
        data.errors["xmlcon"] = "no configuration file"
        return data
    try:
        data.config = parse_xmlcon(cast.config)
    except XmlconError as error:
        data.errors["xmlcon"] = str(error)
        return data
    if cast.raw is None:
        data.errors["hex"] = "no raw data file"
        return data

    config = data.config
    try:
        raw = idata.read_hex_file(
            cast.raw,
            idata.InstrumentType.SBE911Plus,
            config.enabled_sensors(),
            frequency_channels_suppressed=config.frequency_channels_suppressed,
            voltage_words_suppressed=config.voltage_words_suppressed,
        )
    except (ValueError, IndexError) as error:
        data.errors["hex"] = f"unreadable with this configuration: {error}"
        return data
    if raw.sizes.get("scan", 0) == 0:
        data.errors["hex"] = "no data scans"
        return data
    if data.header is None:
        data.header = parse_header(cast.raw)

    try:
        data.time = np.arange(raw.sizes["scan"]) * config.sample_interval
        data.pressure = conversion.convert_pressure_digiquartz(
            raw["digiquartz pressure"].values,
            raw["temperature compensation"].values,
            config.pressure_coefficients(),
            "dbar",
            config.sample_interval,
        )
        data.temperature = _temperature(raw["temperature"].values, config, "temperature")
        data.conductivity = _conductivity(
            raw["conductivity"].values, data.temperature, data.pressure, config, "conductivity"
        )
        data.salinity = _salinity(data.conductivity, data.temperature, data.pressure)
        if config.has_secondary_pair:
            data.temperature2 = _temperature(
                raw["secondary temperature"].values, config, "temperature2"
            )
            data.conductivity2 = _conductivity(
                raw["secondary conductivity"].values,
                data.temperature2,
                data.pressure,
                config,
                "conductivity2",
            )
            data.salinity2 = _salinity(data.conductivity2, data.temperature2, data.pressure)
    except (XmlconError, KeyError, ValueError) as error:
        data.errors["conversion"] = str(error)

    if "NMEA Latitude" in raw:
        data.latitude = raw["NMEA Latitude"].values
        data.longitude = raw["NMEA Longitude"].values
    nibble = status.reconstruct_nibble(raw)
    if nibble is not None:
        data.pump = status.pump_on(nibble)
        data.bottle_confirm = status.bottle_confirm(nibble)
    if "data integrity" in raw:
        data.modulo = raw["data integrity"].values.astype(int)
    return data
