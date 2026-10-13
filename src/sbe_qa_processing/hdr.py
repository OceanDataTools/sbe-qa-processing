"""Parses Seasave .hdr header files (also the header block at the top of a .hex file)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing.readable import Readable, latitude, longitude, timestamp

# e.g. "32 36.25 N" or "117 22.04 W"
_COORDINATE = re.compile(r"^\s*(\d+)\s+(\d+(?:\.\d+)?)\s*([NSEW])\s*$", re.IGNORECASE)
_TIME_FORMATS = ("%b %d %Y %H:%M:%S",)


@dataclass
class Header(Readable):
    values: dict[str, str] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        return self.values.get(key.lower())

    @property
    def temperature_serial(self) -> str | None:
        return self.get("Temperature SN")

    @property
    def conductivity_serial(self) -> str | None:
        return self.get("Conductivity SN")

    @property
    def latitude(self) -> float | None:
        return parse_coordinate(self.get("NMEA Latitude"))

    @property
    def longitude(self) -> float | None:
        return parse_coordinate(self.get("NMEA Longitude"))

    @property
    def nmea_time(self) -> datetime | None:
        return parse_time(self.get("NMEA UTC (Time)"))

    @property
    def system_time(self) -> datetime | None:
        return parse_time(self.get("System UTC")) or parse_time(self.get("System UpLoad Time"))

    @property
    def cast_time(self) -> datetime | None:
        """NMEA time when available, else the deck unit's system time"""
        return self.nmea_time or self.system_time

    @property
    def lat_lon_appended(self) -> bool:
        return "every scan" in (self.get("Store Lat/Lon Data") or "").lower()

    def __str__(self) -> str:
        source = "NMEA" if self.nmea_time else "system clock"
        lines = [
            f"cast time: {timestamp(self.cast_time)}" + (f" ({source})" if self.cast_time else "")
        ]
        if self.latitude is not None and self.longitude is not None:
            lines.append(f"position: {latitude(self.latitude)}, {longitude(self.longitude)}")
        serials = [
            f"{which} SN {serial}"
            for which, serial in (
                ("temperature", self.temperature_serial),
                ("conductivity", self.conductivity_serial),
            )
            if serial
        ]
        if serials:
            lines.append(", ".join(serials))
        lines.append(f"{len(self.values)} header values")
        return "\n".join(lines)


def parse_coordinate(text: str | None) -> float | None:
    if not text:
        return None
    match = _COORDINATE.match(text)
    if not match:
        return None
    degrees, minutes, hemisphere = int(match[1]), float(match[2]), match[3].upper()
    if minutes >= 60:
        return None
    value = degrees + minutes / 60
    return -value if hemisphere in "SW" else value


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    normalized = " ".join(text.split())
    for fmt in _TIME_FORMATS:
        try:
            # Seasave writes NMEA and system times in UTC
            return datetime.strptime(normalized, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_header_lines(lines: Iterable[str]) -> Header:
    """The header in lines, up to *END*; lines after it aren't read"""
    header = Header()
    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("*END*"):
            break
        header.lines.append(line)
        body = line.lstrip("*").strip()
        if "=" in body:
            key, value = body.split("=", 1)
            header.values.setdefault(key.strip().lower(), value.strip())
    return header


def parse_header(path: Path | str) -> Header:
    """The header of a .hdr, or of a .hex without one: read line by line up to *END*, so a deep
    cast's data isn't loaded
    """
    with open(path, encoding="latin-1") as file:
        return parse_header_lines(file)
