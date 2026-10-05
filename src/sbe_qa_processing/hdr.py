"""Parses Seasave .hdr header files (also the header block at the top of a .hex file)."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# e.g. "32 36.25 N" or "117 22.04 W"
_COORDINATE = re.compile(r"^\s*(\d+)\s+(\d+(?:\.\d+)?)\s*([NSEW])\s*$", re.IGNORECASE)
_TIME_FORMATS = ("%b %d %Y %H:%M:%S",)


@dataclass
class Header:
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


def parse_header_lines(lines: list[str]) -> Header:
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
    with open(path, encoding="latin-1") as file:
        return parse_header_lines(file.readlines())
