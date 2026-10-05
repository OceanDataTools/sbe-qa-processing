"""R2R CTD quality assessment tests and infos, as reported in R2R QA 2.0 XML.

Test names, descriptions, units and bounds follow R2R's reports (r2r-ctd). Where R2R's rules
aren't documented, the choices here are noted and were checked against R2R's ratings for
SP2613 (GREEN) and RR2605 (YELLOW):
- Deck tests (file names with deck/dock/test) are left out of the NAV tests.
- A NAV test is YELLOW when at least Thresholds.yellow_minimum_percent of casts pass.
- The overall rating counts individual checks (per cast or per file) across tests.
"""

from dataclasses import dataclass, field

import numpy as np

from sbe_qa_processing.bottles import parse_bottle_log
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import CruiseConfig
from sbe_qa_processing.fileset import Fileset, md5sum

GREEN, YELLOW, RED, GREY, BLACK = "G", "Y", "R", "N", "X"

OVERALL_DESCRIPTION = (
    "GREEN (G) if 100% of tests PASS, YELLOW (Y) if more than 75% of individual tests PASS, "
    "RED (R) if 75% or fewer of individual tests PASS; GREY (N) if no navigation was included "
    "in the distribution; BLACK (X) if one or more tests could not be run."
)


@dataclass
class Bound:
    name: str
    uom: str
    value: str


@dataclass
class TestResult:
    __test__ = False  # not a pytest test class

    name: str
    description: str
    rating: str
    bounds: list[Bound]
    result: float | None = None
    uom: str = "Percent"
    passed: int = 0  # individual checks, for the overall rating
    total: int = 0
    failures: list[str] = field(default_factory=list)
    # files changed after the manifest was written, so not checked yet (e.g. OpenVDM's MD5
    # summary is updated in parallel with post-transfer hooks)
    pending: list[str] = field(default_factory=list)


@dataclass
class Info:
    name: str
    uom: str
    value: str


@dataclass
class R2RResult:
    rating: str
    tests: list[TestResult]
    infos: list[Info]
    checksum_failures: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return OVERALL_DESCRIPTION


def _percent(passed: int, total: int) -> float:
    return round(100 * passed / total) if total else 0


def _same_serial(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a.strip().lstrip("0").upper() == b.strip().lstrip("0").upper()


def bottles_fired(cast) -> int:
    """Number of bottle fires recorded in a cast's .bl file"""
    return len(parse_bottle_log(cast.bottles)) if cast.bottles is not None else 0


def cast_position(data: CastData) -> tuple[float, float] | None:
    """Header NMEA position, else the first valid position appended to the scans"""
    header = data.header
    if header is not None and header.latitude is not None and header.longitude is not None:
        return header.latitude, header.longitude
    if data.latitude is not None:
        valid = ~(np.isnan(data.latitude) | ((data.latitude == 0) & (data.longitude == 0)))
        if valid.any():
            index = int(np.argmax(valid))
            return float(data.latitude[index]), float(data.longitude[index])
    return None


def nav_for_all_scans(data: CastData) -> bool:
    if data.latitude is None or data.scans == 0:
        return False
    missing = np.isnan(data.latitude) | ((data.latitude == 0) & (data.longitude == 0))
    return not missing.any()


def _nav_rating(passed: int, total: int, yellow_minimum: float) -> str:
    if total and passed == total:
        return GREEN
    return YELLOW if _percent(passed, total) >= yellow_minimum else RED


def presence_test(fileset: Fileset) -> TestResult:
    # Configuration-only entries (a shared or spare .XMLCON) aren't casts
    casts = [c for c in fileset.casts if c.raw is not None or c.header is not None]
    complete = [c for c in casts if c.has_all_raw_files]
    return TestResult(
        name="Presence of All Raw Files",
        description="GREEN if 100% of the casts have .hex/.dat, .con and .hdr files; else RED",
        rating=GREEN if casts and len(complete) == len(casts) else RED,
        result=_percent(len(complete), len(casts)),
        bounds=[Bound("MinimumPercentToPass", "Percent", "100")],
        passed=len(complete),
        total=len(casts),
        failures=[c.name for c in casts if not c.has_all_raw_files],
    )


def checksum_test(fileset: Fileset) -> TestResult:
    description = "GREEN if 100% of the files in the manifest have valid checksums; else RED"
    bounds = [Bound("AllFilesHaveValidChecksum", "Unitless", "True/False")]
    if not fileset.manifest:
        # Nothing to verify against: a plain directory, or a bag without manifest-md5.txt
        return TestResult(
            name="Valid Checksum for All Files in Manifest",
            description=description,
            rating=BLACK,
            bounds=bounds,
            uom="Unitless",
            failures=["no manifest-md5.txt"],
        )
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
    total = len(fileset.manifest) - len(pending)
    return TestResult(
        name="Valid Checksum for All Files in Manifest",
        description=description,
        rating=GREEN if not failures else RED,
        bounds=bounds,
        uom="Unitless",
        passed=total - len(failures),
        total=total,
        failures=failures,
        pending=pending,
    )


def location_test(casts: list[CastData], config: CruiseConfig) -> TestResult:
    description = (
        "GREEN if 100% of the profiles have lat/lon within cruise bounds; YELLOW if a few "
        "profiles without lat/lon; GRAY if no navigation was included in the distribution; "
        "else RED; BLACK if no readable lat/lon for all casts"
    )
    bounds = [Bound("MinimumPercentToPass", "Percent", "100")]
    name = "Lat/Lon within NAV Ranges"
    has_nav = [
        c
        for c in casts
        if (c.header and c.header.get("NMEA Latitude") is not None) or c.latitude is not None
    ]
    if not has_nav:
        return TestResult(name, description, GREY, bounds)
    if config.extent is None:
        return TestResult(name, description, GREY, bounds, failures=["no cruise extent available"])
    positions = {c.name: cast_position(c) for c in casts}
    if all(p is None for p in positions.values()):
        return TestResult(name, description, BLACK, bounds, result=0, total=len(casts))
    passed = [n for n, p in positions.items() if p is not None and config.extent.contains(*p)]
    return TestResult(
        name=name,
        description=description,
        rating=_nav_rating(len(passed), len(casts), config.thresholds.yellow_minimum_percent),
        result=_percent(len(passed), len(casts)),
        bounds=bounds,
        passed=len(passed),
        total=len(casts),
        failures=[n for n in positions if n not in passed],
    )


def date_test(casts: list[CastData], config: CruiseConfig) -> TestResult:
    description = (
        "GREEN if 100% of the profiles have Date within cruise bounds; YELLOW if a few profile "
        "times out of cruise bounds; GRAY if no navigation was provided in the distribution; "
        "else RED; BLACK if no readable dates to test"
    )
    bounds = [Bound("PercentFilesWithValidTemporalRange", "Percent", "100")]
    name = "Dates within NAV Ranges"
    times = {c.name: c.header.cast_time if c.header else None for c in casts}
    if not casts:
        return TestResult(name, description, GREY, bounds)
    if all(t is None for t in times.values()):
        return TestResult(name, description, BLACK, bounds, result=0, total=len(casts))
    passed = [
        n
        for n, t in times.items()
        if t is not None and config.depart_date <= t.date() <= config.arrive_date
    ]
    return TestResult(
        name=name,
        description=description,
        rating=_nav_rating(len(passed), len(casts), config.thresholds.yellow_minimum_percent),
        result=_percent(len(passed), len(casts)),
        bounds=bounds,
        passed=len(passed),
        total=len(casts),
        failures=[n for n in times if n not in passed],
    )


def overall_rating(tests: list[TestResult]) -> str:
    if any(t.rating == BLACK for t in tests):
        return BLACK
    if any(t.rating == GREY for t in tests):
        return GREY
    if all(t.rating == GREEN for t in tests):
        return GREEN
    passed = sum(t.passed for t in tests)
    total = sum(t.total for t in tests)
    return YELLOW if total and 100 * passed / total > 75 else RED


def run_r2r_tests(fileset: Fileset, casts: list[CastData], config: CruiseConfig) -> R2RResult:
    by_name = {c.name: c for c in casts}
    raw_casts = [by_name[c.name] for c in fileset.raw_casts if c.name in by_name]
    science_casts = [c for c in raw_casts if not c.cast.is_deck_test]

    tests = [
        presence_test(fileset),
        checksum_test(fileset),
        location_test(science_casts, config),
        date_test(science_casts, config),
    ]

    def names(casts_):
        return " ".join(c.name for c in casts_)

    deck_tests = [
        (c.raw or c.header or c.config).name
        for c in fileset.casts
        if c.is_deck_test and (c.raw or c.header)
    ]
    temperature_problems = [
        c
        for c in raw_casts
        if c.header
        and c.config
        and c.config.sensor("temperature")
        and not _same_serial(
            c.header.temperature_serial, c.config.sensor("temperature").serial_number
        )
    ]
    conductivity_problems = [
        c
        for c in raw_casts
        if c.header
        and c.config
        and c.config.sensor("conductivity")
        and not _same_serial(
            c.header.conductivity_serial, c.config.sensor("conductivity").serial_number
        )
    ]
    blank_nav = [c for c in science_casts if cast_position(c) is None]
    outside = [
        c
        for c in science_casts
        if config.extent is not None
        and (p := cast_position(c)) is not None
        and not config.extent.contains(*p)
    ]
    models = {c.config.name for c in casts if c.config is not None}

    infos = [
        Info("Total Raw Files", "# of .hex/.dat Files", str(len(fileset.raw_casts))),
        Info(
            "# of Casts with Bottles Fired",
            "Count",
            str(sum(1 for c in fileset.raw_casts if bottles_fired(c) > 0)),
        ),
        Info(
            "Model Number of CTD Instrument",
            "Unitless",
            "SBE911" if any("911" in m for m in models) else ", ".join(sorted(models)),
        ),
        Info(
            "# of Casts with NAV for All Scans",
            "Count",
            str(sum(1 for c in science_casts if nav_for_all_scans(c))),
        ),
        Info("Casts without all Raw Files", "List", " ".join(tests[0].failures)),
        Info(
            "Casts with Hex file in Bad Format",
            "List",
            names(c for c in raw_casts if "hex" in c.errors),
        ),
        Info(
            "Casts with XMLCON/con file in Bad Format",
            "List",
            names(c for c in casts if c.cast.config is not None and "xmlcon" in c.errors),
        ),
        Info("Casts with dock/deck and test in file name", "List", " ".join(deck_tests)),
        Info("Casts with temp. sensor serial number problem", "List", names(temperature_problems)),
        Info(
            "Casts with cond. sensor serial number problem", "List", names(conductivity_problems)
        ),
        Info("Casts with Blank, missing, or unrecognizable NAV", "List", names(blank_nav)),
        Info("Casts that Failed NAV Boundary Tests", "List", names(outside)),
    ]
    return R2RResult(
        rating=overall_rating(tests),
        tests=tests,
        infos=infos,
        checksum_failures=tests[1].failures,
    )
