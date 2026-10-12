"""QA configuration. Every field is optional: the QA runs without a config at all.

A config TOML looks like::

    [cruise]
    id = "SP2613"           # the reports' title; default: the fileset directory's name
    name = "..."
    vessel_name = "Sproul"
    depart_date = 2026-07-09   # expected dates, for the integrity checks
    arrive_date = 2026-07-09

    [cruise.extent]         # expected area, for the integrity checks and the map
    westernmost = -117.38
    easternmost = -117.22
    southernmost = 32.59
    northernmost = 32.71

    [thresholds]            # optional, defaults below
    temperature_difference = 0.01

The R2R integration's cruise TOML is the same file with R2R's IDs, ports, fileset ID and
provenance added (integrations/r2r/config.py); load_config ignores those.
"""

import tomllib
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path


@dataclass
class Extent:
    """Cruise bounding box in decimal degrees (longitude east positive). As in R2R's reports, a
    box across the antimeridian has westernmost > easternmost (e.g. 178 to -178)
    """

    westernmost: float
    easternmost: float
    southernmost: float
    northernmost: float

    @property
    def crosses_antimeridian(self) -> bool:
        return self.westernmost > self.easternmost

    def contains(self, latitude: float, longitude: float) -> bool:
        if not self.southernmost <= latitude <= self.northernmost:
            return False
        if self.crosses_antimeridian:
            return longitude >= self.westernmost or longitude <= self.easternmost
        return self.westernmost <= longitude <= self.easternmost


@dataclass
class Thresholds:
    """Pass limits for the science QA checks. Differences are primary minus secondary sensor,
    evaluated below `dual_sensor_min_pressure` where the water column is usually stable
    """

    # [deg C, S/m, PSU] median of |primary - secondary|
    temperature_difference: float = 0.01
    conductivity_difference: float = 0.001
    salinity_difference: float = 0.01
    # [dbar] ignore the surface layer for dual-sensor statistics
    dual_sensor_min_pressure: float = 20.0
    # plausible ranges
    temperature_range: tuple[float, float] = (-2.5, 40.0)
    conductivity_range: tuple[float, float] = (0.0, 7.0)
    salinity_range: tuple[float, float] = (2.0, 42.0)
    pressure_range: tuple[float, float] = (-5.0, 11000.0)
    # [%] maximum share of scans flagged by Wild Edit (2/20 std dev, 100-scan blocks)
    max_spike_percent: float = 0.5
    # [%] maximum share of scans lost (modulo count gaps)
    max_lost_scan_percent: float = 0.1
    # [days] calibrations older than this at the time of the cast are flagged
    max_calibration_age_days: int = 365


@dataclass(kw_only=True)
class QAConfig:
    """What the core QA uses. Every field is optional"""

    cruise_id: str = ""  # the reports' title; run_qa defaults it to the fileset directory's name
    cruise_name: str = ""
    # Report header metadata, shown when given
    cruise_pi: str = ""
    cruise_location: str = ""
    vessel_name: str = ""
    contact_institution: str = ""
    contact_email: str = ""
    # Expected area and dates. Without them, the integrity checks that need them are n/a
    extent: Extent | None = None
    depart_date: date | None = None
    arrive_date: date | None = None
    # Checksum manifest for a plain directory (e.g. OpenVDM's MD5 summary): "<md5> <path>" lines,
    # with paths relative to the manifest's directory. A BagIt bag uses its own manifest-md5.txt
    manifest_path: Path | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    # The [theme] table, checked and with absolute paths (theme.py); applied over a --theme file
    theme: dict = field(default_factory=dict)

    @property
    def identifier(self) -> str:
        """The reports' file name prefix"""
        return self.cruise_id


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


# [thresholds] keys the integrations read, not core Thresholds
INTEGRATION_THRESHOLDS = ("yellow_minimum_percent",)


def thresholds_from_table(table: dict) -> Thresholds:
    known = {f.name for f in fields(Thresholds)}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"unknown thresholds: {', '.join(sorted(unknown))}")
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return Thresholds(**values)


def load_config(path: Path | str) -> QAConfig:
    """A config TOML. Tables and keys only the integrations use (e.g. R2R's IDs and ports) are
    ignored
    """
    from sbe_qa_processing.theme import theme_table  # theme.py imports this module

    with open(path, "rb") as file:
        data = tomllib.load(file)
    cruise = data.get("cruise", {})
    provenance = data.get("provenance", {})
    manifest = data.get("fileset", {}).get("manifest")
    thresholds = {
        k: v for k, v in data.get("thresholds", {}).items() if k not in INTEGRATION_THRESHOLDS
    }
    return QAConfig(
        cruise_id=str(cruise.get("id", "")),
        cruise_name=cruise.get("name", ""),
        cruise_pi=cruise.get("pi", ""),
        cruise_location=cruise.get("location", ""),
        vessel_name=cruise.get("vessel_name", ""),
        contact_institution=provenance.get("contact_institution", ""),
        contact_email=provenance.get("contact_email", ""),
        extent=Extent(**cruise["extent"]) if "extent" in cruise else None,
        depart_date=as_date(cruise["depart_date"]) if "depart_date" in cruise else None,
        arrive_date=as_date(cruise["arrive_date"]) if "arrive_date" in cruise else None,
        manifest_path=(Path(path).parent / manifest).resolve() if manifest else None,
        thresholds=thresholds_from_table(thresholds),
        theme=theme_table(data.get("theme", {}), Path(path).parent, str(path)),
    )


def config_to_toml(config: QAConfig) -> str:
    """The config as TOML that load_config reads back, e.g. to snapshot what a hook resolved"""
    from sbe_qa_processing.theme import theme_toml  # theme.py imports this module

    cruise = {
        "id": config.cruise_id,
        "name": config.cruise_name,
        "pi": config.cruise_pi,
        "location": config.cruise_location,
        "vessel_name": config.vessel_name,
    }
    lines = ["[cruise]"] + [f"{k} = {toml_value(v)}" for k, v in cruise.items() if v]
    lines += [
        f"{which} = {value}"
        for which, value in (
            ("depart_date", config.depart_date),
            ("arrive_date", config.arrive_date),
        )
        if value is not None
    ]
    if config.extent:
        lines += ["", "[cruise.extent]"]
        lines += [f"{k} = {getattr(config.extent, k)}" for k in fields_of(Extent)]
    if config.manifest_path:
        lines += ["", "[fileset]", f"manifest = {toml_value(str(config.manifest_path))}"]
    provenance = {
        k: getattr(config, k)
        for k in ("contact_institution", "contact_email")
        if getattr(config, k)
    }
    if provenance:
        lines += ["", "[provenance]"] + [f"{k} = {toml_value(v)}" for k, v in provenance.items()]
    lines += thresholds_toml(config.thresholds)
    lines += theme_toml(config.theme)
    return "\n".join(lines) + "\n"


def thresholds_toml(thresholds: Thresholds) -> list[str]:
    """A [thresholds] table of the values that differ from the defaults, or nothing"""
    defaults = Thresholds()
    changed = {
        f.name: getattr(thresholds, f.name)
        for f in fields(Thresholds)
        if getattr(thresholds, f.name) != getattr(defaults, f.name)
    }
    if not changed:
        return []
    lines = ["", "[thresholds]"]
    for key, value in changed.items():
        value = list(value) if isinstance(value, tuple) else value
        lines.append(f"{key} = {value}")
    return lines


def fields_of(cls) -> list[str]:
    return [f.name for f in fields(cls)]


def toml_value(value) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)
