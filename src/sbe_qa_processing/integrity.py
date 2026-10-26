"""Integrity checks for any fileset: raw files present and readable, checksums, positions, cast
times and sensor serial numbers.

The per-cast predicates here are shared with R2R's tests (integrations/r2r/rules.py), which
rate them by R2R's rules. These checks use the science checks' pass/warn/fail/info/n/a
instead, need no config, and report n/a where a check needs an expected area or dates the
config doesn't give. Unlike R2R, they also flag invalid positions, implausible cast times and
casts out of time order.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

import numpy as np

from sbe_qa_processing.bottles import parse_bottle_log
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Extent, QAConfig
from sbe_qa_processing.fileset import (
    HEADER_SUFFIX,
    RAW_DATA_SUFFIXES,
    Cast,
    Fileset,
    md5sum,
)
from sbe_qa_processing.readable import Readable, block
from sbe_qa_processing.science import FAIL, INFO, NA, PASS, WARN, CheckResult

# Cast times before this are a reset or unset clock: the SBE 911plus dates from the 1990s
EARLIEST_CAST_TIME = datetime(1990, 1, 1, tzinfo=UTC)
# Worst first, for a fileset summary of a per-cast check
_SEVERITY = (FAIL, WARN, PASS, INFO, NA)
_FILE_KINDS = {"xmlcon": ".XMLCON/.con", "hex": ".hex/.dat", "conversion": "conversion"}


@dataclass
class IntegrityResult(Readable):
    fileset: list[CheckResult]  # fileset checks and per-cast summaries, then infos
    casts: dict[str, list[CheckResult]]  # by cast name, deck tests included

    def __str__(self) -> str:
        sections = [block("Fileset", [str(check) for check in self.fileset])]
        sections += [
            block(name, [str(check) for check in checks]) for name, checks in self.casts.items()
        ]
        return "\n".join(sections)


# Predicates shared with integrations/r2r/rules.py


def missing_nav(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    """Scans whose appended NMEA position is blank (NaN or 0,0)"""
    return np.isnan(latitude) | ((latitude == 0) & (longitude == 0))


def cast_position(data: CastData) -> tuple[float, float] | None:
    """Header NMEA position, else the first valid position appended to the scans"""
    header = data.header
    if header is not None and header.latitude is not None and header.longitude is not None:
        return header.latitude, header.longitude
    if data.latitude is not None:
        valid = ~missing_nav(data.latitude, data.longitude)
        if valid.any():
            index = int(np.argmax(valid))
            return float(data.latitude[index]), float(data.longitude[index])
    return None


def nav_for_all_scans(data: CastData) -> bool:
    if data.latitude is None or data.scans == 0:
        return False
    return not missing_nav(data.latitude, data.longitude).any()


def bottles_fired(cast: Cast) -> int:
    """Number of bottle fires recorded in a cast's .bl file"""
    return len(parse_bottle_log(cast.bottles)) if cast.bottles is not None else 0


def same_serial(a: str | None, b: str | None) -> bool:
    """Serial numbers match, ignoring case and leading zeros"""
    if not a or not b:
        return False
    return a.strip().lstrip("0").upper() == b.strip().lstrip("0").upper()


def verify_manifest(fileset: Fileset) -> tuple[list[str], list[str]]:
    """Manifest paths that are missing or fail their checksum, and paths changed after the
    manifest was written, so not checked yet (OpenVDM updates its MD5 summary in parallel with
    post-transfer hooks)
    """
    failures, pending = [], []
    for entry in fileset.manifest:
        path = fileset.root / entry.path
        if (
            path.exists()
            and fileset.manifest_mtime is not None
            and path.stat().st_mtime > fileset.manifest_mtime
        ):
            pending.append(entry.path)
        elif not path.exists() or md5sum(path) != entry.md5:
            failures.append(entry.path)
    return failures, pending


# Per-cast checks


def valid_position(latitude: float, longitude: float) -> bool:
    """In range, and not 0,0 (a blank NMEA position)"""
    in_range = -90 <= latitude <= 90 and -180 <= longitude <= 180
    return in_range and not latitude == longitude == 0


def raw_files_check(cast: Cast) -> CheckResult:
    name = "Raw files complete"
    files = ((".hex/.dat", cast.raw), (".XMLCON/.con", cast.config), (".hdr", cast.header))
    missing = [kind for kind, path in files if path is None]
    detail = ".bl bottle log present" if cast.bottles is not None else "no .bl bottle log"
    if missing:
        return CheckResult(name, FAIL, f"missing {', '.join(missing)}", detail=detail)
    return CheckResult(name, PASS, "all present", detail=detail)


def readable_check(data: CastData) -> CheckResult:
    name = "Files readable"
    if data.cast.raw is None or data.cast.config is None:
        return CheckResult(name, NA, "raw data or configuration file missing")
    if data.errors:
        failed = ", ".join(_FILE_KINDS.get(kind, kind) for kind in data.errors)
        return CheckResult(name, FAIL, f"failed: {failed}", detail="; ".join(data.errors.values()))
    return CheckResult(name, PASS, f"{data.scans} scans")


def position_check(data: CastData) -> CheckResult:
    name = "Cast position"
    position = cast_position(data)
    if position is None:
        return CheckResult(name, FAIL, "none", detail="no NMEA position in the header or scans")
    latitude, longitude = position
    value = f"{latitude:.4f}, {longitude:.4f}"
    header = data.header
    from_header = header is not None and header.latitude is not None
    if not valid_position(latitude, longitude):
        return CheckResult(name, FAIL, value, detail="not a valid position")
    return CheckResult(name, PASS, value, detail="header" if from_header else "first valid scan")


def nav_every_scan_check(data: CastData) -> CheckResult:
    name = "NMEA position on every scan"
    if data.latitude is None:
        return CheckResult(name, NA, "not appended")
    missing = missing_nav(data.latitude, data.longitude)
    return CheckResult(
        name, PASS if not missing.any() else WARN, f"{int(missing.sum())} scans without position"
    )


def _extent_text(extent: Extent) -> str:
    return (
        f"{extent.westernmost:g} to {extent.easternmost:g} E, "
        f"{extent.southernmost:g} to {extent.northernmost:g} N"
    )


def area_check(data: CastData, config: QAConfig) -> CheckResult:
    name = "Position inside the expected area"
    if config.extent is None:
        return CheckResult(name, NA, "no expected area in the config")
    if data.cast.is_deck_test:
        return CheckResult(name, NA, "deck test")
    position = cast_position(data)
    if position is None:
        return CheckResult(name, NA, "no position")
    inside = config.extent.contains(*position)
    return CheckResult(
        name,
        PASS if inside else FAIL,
        "inside" if inside else "outside",
        _extent_text(config.extent),
    )


def time_check(data: CastData, now: datetime) -> CheckResult:
    name = "Cast time"
    header = data.header
    time = header.cast_time if header is not None else None
    if time is None:
        return CheckResult(name, FAIL, "none", detail="no NMEA or system time in the header")
    value = f"{time:%Y-%m-%d %H:%M:%S} UTC"
    if time < EARLIEST_CAST_TIME:
        return CheckResult(
            name, FAIL, value, detail=f"before {EARLIEST_CAST_TIME:%Y}: an unset clock?"
        )
    if time > now:
        return CheckResult(name, FAIL, value, detail="in the future")
    source = "NMEA time" if header.nmea_time is not None else "deck unit clock (no NMEA time)"
    return CheckResult(name, PASS, value, detail=source)


def dates_check(data: CastData, config: QAConfig) -> CheckResult:
    name = "Cast time inside the expected dates"
    if config.depart_date is None or config.arrive_date is None:
        return CheckResult(name, NA, "no expected dates in the config")
    if data.cast.is_deck_test:
        return CheckResult(name, NA, "deck test")
    time = data.header.cast_time if data.header is not None else None
    if time is None:
        return CheckResult(name, NA, "no cast time")
    inside = config.depart_date <= time.date() <= config.arrive_date
    return CheckResult(
        name,
        PASS if inside else FAIL,
        f"{time:%Y-%m-%d}",
        f"{config.depart_date} to {config.arrive_date}",
    )


def serial_check(data: CastData) -> CheckResult:
    name = "Header serial numbers match the configuration"
    if data.header is None or data.config is None:
        return CheckResult(name, NA, "needs the header and the configuration")
    matched, mismatched, unlisted = [], [], []
    for label, channel, header_serial in (
        ("T", "temperature", data.header.temperature_serial),
        ("C", "conductivity", data.header.conductivity_serial),
    ):
        sensor = data.config.sensor(channel)
        if sensor is None:
            continue
        if not header_serial:
            unlisted.append(label)
        elif same_serial(header_serial, sensor.serial_number):
            matched.append(f"{label} {sensor.serial_number}")
        else:
            mismatched.append(
                f"{label}: header {header_serial}, configuration {sensor.serial_number}"
            )
    unlisted_text = f"{'/'.join(unlisted)} not in the header" if unlisted else ""
    if mismatched:
        return CheckResult(name, FAIL, "; ".join(mismatched), detail=unlisted_text)
    if unlisted:
        return CheckResult(name, WARN, unlisted_text, detail=", ".join(matched))
    if not matched:
        return CheckResult(name, NA, "no primary T/C sensor in the configuration")
    return CheckResult(name, PASS, ", ".join(matched))


def run_cast_checks(data: CastData, config: QAConfig, now: datetime) -> list[CheckResult]:
    return [
        raw_files_check(data.cast),
        readable_check(data),
        position_check(data),
        nav_every_scan_check(data),
        area_check(data, config),
        time_check(data, now),
        dates_check(data, config),
        serial_check(data),
    ]


# Fileset checks


def checksum_check(fileset: Fileset) -> CheckResult:
    name = "Checksums match the manifest"
    if not fileset.manifest:
        return CheckResult(
            name, NA, "no manifest", detail="a bag's manifest-md5.txt, or [fileset] manifest"
        )
    failures, pending = verify_manifest(fileset)
    checked = len(fileset.manifest) - len(pending)
    details = []
    if failures:
        details.append(f"missing or changed: {', '.join(failures)}")
    if pending:
        details.append(f"{len(pending)} changed after the manifest was written, not checked")
    return CheckResult(
        name,
        FAIL if failures else PASS,
        f"{checked - len(failures)} of {checked} files",
        detail="; ".join(details),
    )


def _natural_key(name: str) -> list:
    """CAST2 before CAST10"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def order_check(casts: list[CastData]) -> CheckResult:
    name = "Casts in time order"
    timed = [
        (data.name, data.header.cast_time)
        for data in sorted(casts, key=lambda d: _natural_key(d.name))
        if not data.cast.is_deck_test and data.header is not None and data.header.cast_time
    ]
    if len(timed) < 2:
        return CheckResult(name, NA, "fewer than two casts with a time")
    earlier = [cast for (_, before), (cast, time) in pairwise(timed) if time < before]
    if earlier:
        return CheckResult(
            name,
            WARN,
            f"{len(earlier)} earlier than the cast before",
            "file name order",
            ", ".join(earlier),
        )
    return CheckResult(name, PASS, "file name order is time order", "file name order")


def summarize(name: str, results: dict[str, CheckResult]) -> CheckResult:
    """One per-cast check across the fileset: its worst status, and the casts that didn't pass"""
    applicable = {cast: r for cast, r in results.items() if r.status != NA}
    if not applicable:
        reasons = {r.value for r in results.values()}
        return CheckResult(name, NA, reasons.pop() if len(reasons) == 1 else "n/a for every cast")
    status = min((r.status for r in applicable.values()), key=_SEVERITY.index)
    passed = sum(1 for r in applicable.values() if r.status == PASS)
    limits = {r.limit for r in applicable.values()}
    return CheckResult(
        name,
        status,
        f"{passed} of {len(applicable)} casts pass",
        limits.pop() if len(limits) == 1 else "",
        "; ".join(
            f"{cast}: {r.value}" for cast, r in applicable.items() if r.status in (WARN, FAIL)
        ),
    )


def bounding_extent(positions: list[tuple[float, float]]) -> Extent | None:
    """The smallest box around (latitude, longitude) positions; across the antimeridian
    (westernmost > easternmost) when that's smaller
    """
    if not positions:
        return None
    latitudes = [latitude for latitude, _ in positions]
    longitudes = sorted(longitude for _, longitude in positions)
    # Leave out the widest gap between neighbouring longitudes, around the globe
    n = len(longitudes)
    gaps = [(longitudes[(i + 1) % n] - longitudes[i]) % 360 for i in range(n)]
    widest = max(range(n), key=gaps.__getitem__)
    return Extent(
        westernmost=longitudes[(widest + 1) % n],
        easternmost=longitudes[widest],
        southernmost=min(latitudes),
        northernmost=max(latitudes),
    )


def _coverage(casts: list[CastData], now: datetime) -> list[CheckResult]:
    """When and where the casts were, from their valid positions and plausible times. Deck
    tests are left out
    """
    stations = [data for data in casts if not data.cast.is_deck_test]
    times = sorted(
        time
        for data in stations
        if data.header is not None
        and (time := data.header.cast_time) is not None
        and EARLIEST_CAST_TIME <= time <= now
    )
    positions = [
        position
        for data in stations
        if (position := cast_position(data)) is not None and valid_position(*position)
    ]
    extent = bounding_extent(positions)
    return [
        CheckResult(
            "Cast times",
            INFO,
            f"{times[0]:%Y-%m-%d %H:%M} to {times[-1]:%Y-%m-%d %H:%M} UTC" if times else "none",
        ),
        CheckResult("Cast positions", INFO, _extent_text(extent) if extent else "none"),
    ]


def _cast_count(fileset: Fileset, casts: list[CastData]) -> CheckResult:
    """How many casts; none warns, as the directory is most likely the wrong one"""
    if casts:
        return CheckResult("Casts", INFO, str(len(casts)))
    *others, last = (*RAW_DATA_SUFFIXES, HEADER_SUFFIX)
    suffixes = f"{', '.join(others)} or {last}"
    return CheckResult("Casts", WARN, "0", detail=f"no {suffixes} files in {fileset.data_dir}")


def _infos(casts: list[CastData]) -> list[CheckResult]:
    deck_tests = [d.name for d in casts if d.cast.is_deck_test]
    with_bottles = [d.name for d in casts if d.cast.raw is not None and bottles_fired(d.cast)]
    models = sorted({d.config.name for d in casts if d.config is not None})
    return [
        CheckResult("Deck-test casts", INFO, str(len(deck_tests)), detail=", ".join(deck_tests)),
        CheckResult(
            "Casts with bottles fired",
            INFO,
            str(len(with_bottles)),
            detail=", ".join(with_bottles),
        ),
        CheckResult("Instrument model", INFO, ", ".join(models) or "unknown"),
    ]


def run_integrity_checks(
    fileset: Fileset, casts: list[CastData], config: QAConfig, now: datetime | None = None
) -> IntegrityResult:
    """Checks for every cast (configuration-only entries aren't casts) and for the fileset"""
    now = now or datetime.now(UTC)
    per_cast = {data.name: run_cast_checks(data, config, now) for data in casts}
    names = [check.name for check in next(iter(per_cast.values()), [])]
    summaries = [
        summarize(name, {cast: checks[i] for cast, checks in per_cast.items()})
        for i, name in enumerate(names)
    ]
    return IntegrityResult(
        fileset=[
            checksum_check(fileset),
            *summaries,
            order_check(casts),
            *_coverage(casts, now),
            _cast_count(fileset, casts),
            *_infos(casts),
        ],
        casts=per_cast,
    )
