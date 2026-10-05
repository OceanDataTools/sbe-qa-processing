"""Bottle fires from Seasave's .bl bottle log, with the pressure at each fire.

A .bl file has the file name, a RESET line, then one line per fire:

    firing sequence, bottle position, time, first scan, last scan

The scan range (about 1.5 s) is what SBE Data Processing averages for its bottle files. The scan
numbers are 0-based indexes into the cast's scans: in real 911plus data each bottle-confirm
status bit starts within 0-2 scans of its fire's first scan.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from sbe_qa_processing.cast import CastData
from sbe_qa_processing.hdr import parse_time


@dataclass
class BottleFire:
    sequence: int
    position: int
    time: datetime | None
    first_scan: int
    last_scan: int
    pressure: float | None = None  # [dbar] mean over the fire's scans
    pressure_min: float | None = None
    pressure_max: float | None = None


def parse_bottle_log(path: Path | str) -> list[BottleFire]:
    """The fires recorded in a .bl file (unreadable lines are skipped)"""
    fires = []
    for line in Path(path).read_text(errors="replace").splitlines()[1:]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5 or line.upper().startswith("RESET"):
            continue
        try:
            fires.append(
                BottleFire(
                    sequence=int(parts[0]),
                    position=int(parts[1]),
                    time=parse_time(parts[2]),
                    first_scan=int(parts[3]),
                    last_scan=int(parts[4]),
                )
            )
        except ValueError:
            continue
    return fires


def bottle_fires(data: CastData) -> list[BottleFire]:
    """A cast's bottle fires with the pressure over each fire's scans, in firing order"""
    if data.cast.bottles is None:
        return []
    fires = parse_bottle_log(data.cast.bottles)
    if data.pressure is not None:
        for fire in fires:
            scans = data.pressure[max(fire.first_scan, 0) : fire.last_scan + 1]
            if scans.size:
                fire.pressure = float(np.mean(scans))
                fire.pressure_min = float(np.min(scans))
                fire.pressure_max = float(np.max(scans))
    return sorted(fires, key=lambda fire: fire.sequence)
