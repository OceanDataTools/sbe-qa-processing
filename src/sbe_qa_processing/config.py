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
provenance added (integrations/r2r/config.py); load_config accepts those and leaves them to it
(`INTEGRATION_KEYS`). Any other key, or a value of the wrong shape, is a ConfigError that says
where it belongs, e.g. a top-level `depart_date` (`[cruise] depart_date`).
"""

import difflib
import tomllib
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

from sbe_qa_processing.readable import Readable, latitude, longitude


@dataclass
class Extent(Readable):
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

    def __str__(self) -> str:
        across = " (across the antimeridian)" if self.crosses_antimeridian else ""
        return (
            f"{latitude(self.southernmost)} to {latitude(self.northernmost)}, "
            f"{longitude(self.westernmost)} to {longitude(self.easternmost)}{across}"
        )


@dataclass
class Thresholds(Readable):
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

    def __str__(self) -> str:
        defaults = Thresholds()
        lines = []
        for f in fields(self):
            value, default = getattr(self, f.name), getattr(defaults, f.name)
            unit = f" {THRESHOLD_UNITS[f.name]}" if THRESHOLD_UNITS.get(f.name) else ""
            changed = "" if value == default else f" (default {_threshold(default)})"
            lines.append(f"{f.name}: {_threshold(value)}{unit}{changed}")
        return "\n".join(lines)


THRESHOLD_UNITS = {
    "temperature_difference": "°C",
    "conductivity_difference": "S/m",
    "salinity_difference": "PSU",
    "dual_sensor_min_pressure": "dbar",
    "temperature_range": "°C",
    "conductivity_range": "S/m",
    "salinity_range": "PSU",
    "pressure_range": "dbar",
    "max_spike_percent": "%",
    "max_lost_scan_percent": "%",
    "max_calibration_age_days": "days",
}


def _threshold(value) -> str:
    return f"{value[0]:g} to {value[1]:g}" if isinstance(value, tuple) else f"{value:g}"


@dataclass(kw_only=True)
class QAConfig(Readable):
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

    def __str__(self) -> str:
        """The config as TOML, as load_config reads it"""
        return config_to_toml(self).rstrip("\n")


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
        raise ConfigError(f"unknown thresholds: {', '.join(sorted(unknown))}")
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return Thresholds(**values)


def extent_from_table(table: dict, where: str) -> Extent:
    """An extent table (`where` names it, e.g. "config.toml: [cruise.extent]") with every edge"""
    missing = [edge for edge in fields_of(Extent) if edge not in table]
    if missing:
        raise ConfigError(f"{where} needs {', '.join(missing)}")
    return Extent(**table)


def date_from_table(table: dict, key: str, where: str) -> date | None:
    """A date in a table (`where` names it, e.g. "config.toml: [cruise]"), or None"""
    if key not in table:
        return None
    try:
        return as_date(table[key])
    except ValueError:
        raise ConfigError(
            f"{where} {key} must be a date like 2026-07-09, not {table[key]!r}"
        ) from None


def load_config(path: Path | str) -> QAConfig:
    """A config TOML. The keys only R2R reads (its IDs and ports) are accepted and left to it;
    any other unknown key, or a value of the wrong shape, is a ConfigError
    """
    from sbe_qa_processing.theme import theme_table  # theme.py imports this module

    data = read_toml(path)
    check_keys(data, CONFIG_KEYS, str(path))
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
        extent=(
            extent_from_table(cruise["extent"], f"{path}: [cruise.extent]")
            if "extent" in cruise
            else None
        ),
        depart_date=date_from_table(cruise, "depart_date", f"{path}: [cruise]"),
        arrive_date=date_from_table(cruise, "arrive_date", f"{path}: [cruise]"),
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


class ConfigError(ValueError):
    """A config, cruise or site TOML that can't be read or used, as a one-line message"""


def read_toml(path: Path | str, what: str = "config TOML") -> dict:
    """A TOML file's tables, with a missing file or bad TOML as a ConfigError naming the path"""
    try:
        with open(path, "rb") as file:
            return tomllib.load(file)
    except FileNotFoundError:
        raise ConfigError(f"no {what} at {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None


# Key schemas: a key maps to KEY (a value), a schema (a table) or CHECKED (a table whose own
# loader checks its keys: [theme], whose keys theme.py has, and which imports this module)
KEY, CHECKED = "key", "checked"
EXTENT_KEYS = dict.fromkeys(fields_of(Extent), KEY)
THRESHOLD_KEYS = dict.fromkeys(fields_of(Thresholds), KEY)
CORE_KEYS = {
    "cruise": dict.fromkeys(
        ("id", "name", "pi", "location", "vessel_name", "depart_date", "arrive_date"), KEY
    )
    | {"extent": EXTENT_KEYS},
    "fileset": {"manifest": KEY},
    "provenance": dict.fromkeys(("contact_institution", "contact_email"), KEY),
    "thresholds": THRESHOLD_KEYS,
    "theme": CHECKED,
}
# The keys R2R's cruise TOML adds (integrations/r2r/config.py), which core leaves to it. Written
# out here, as core never imports an integration; tests/test_r2r_config.py keeps them in step
PORT_KEYS = dict.fromkeys(
    ("name", "port_id", "country_id", "state_id", "longitude", "latitude"), KEY
)
INTEGRATION_KEYS = {
    "cruise": dict.fromkeys(("vessel_id", "operator_id", "scheduler_id"), KEY)
    | {"depart_port": PORT_KEYS, "arrive_port": PORT_KEYS},
    "fileset": {"id": KEY},
    "provenance": dict.fromkeys(("distro_type", "contact_institution_id"), KEY),
    "thresholds": dict.fromkeys(INTEGRATION_THRESHOLDS, KEY),
}


def merge_keys(*schemas: dict) -> dict:
    """The schemas' keys together, tables merged"""
    merged: dict = {}
    for schema in schemas:
        for key, value in schema.items():
            mine = merged.get(key)
            merged[key] = (
                merge_keys(mine, value)
                if isinstance(mine, dict) and isinstance(value, dict)
                else value
            )
    return merged


CONFIG_KEYS = merge_keys(CORE_KEYS, INTEGRATION_KEYS)


def _paths(schema: dict, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], bool]]:
    """Every path in the schema, tables as well as keys, each with whether it's a table"""
    paths = []
    for key, value in schema.items():
        here = (*path, key)
        paths.append((here, value != KEY))
        if isinstance(value, dict):
            paths += _paths(value, here)
    return paths


def _where_in(schema: dict, path: tuple[str, ...]) -> str:
    """How a TOML writes a schema path: a table as [table], a key as [table] key, or at the top
    level
    """
    node: object = schema
    for key in path:
        node = node[key]  # type: ignore[index]
    if node != KEY:
        return f"[{'.'.join(path)}]"
    *table, leaf = path
    return f"[{'.'.join(table)}] {leaf}" if table else f"{leaf} at the top level"


def check_keys(data: dict, schema: dict, where: str) -> None:
    """Raises ConfigError naming every key of data the schema doesn't know, each with where it
    belongs when the schema has it elsewhere (a top-level `depart_date`: `[cruise]
    depart_date`), or the table's key it's closest to (`depart_dat`: `depart_date`); and every
    value of the wrong shape, a value where a table belongs or a table where a value does,
    which the loaders would otherwise fail on with a traceback
    """
    unknown: list[tuple[tuple[str, ...], str, dict, object]] = []
    shapes: list[str] = []

    def name(path: tuple[str, ...], key: str) -> str:
        return f"[{'.'.join(path)}] {key}" if path else f"{key} (top level)"

    def walk(table: dict, known: dict, path: tuple[str, ...]) -> None:
        for key, value in table.items():
            if key not in known:
                unknown.append((path, key, known, value))
                continue
            here = (*path, key)
            if known[key] == KEY:
                if isinstance(value, dict) or (
                    isinstance(value, list) and any(isinstance(v, dict) for v in value)
                ):
                    shapes.append(f"{name(path, key)} must be a value, not a table")
            elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                table_name = ".".join(here)
                shapes.append(f"[[{table_name}]] must be one table, [{table_name}]")
            elif not isinstance(value, dict):
                shapes.append(f"{name(path, key)} must be a table, not a value")
            elif isinstance(known[key], dict):
                walk(value, known[key], here)

    walk(data, schema, ())
    if not unknown and not shapes:
        return
    paths = _paths(schema)

    def described(path: tuple[str, ...], key: str, known: dict, value: object) -> str:
        # The key elsewhere in the schema, as a key or a whole table (a top-level [extent]:
        # [cruise.extent]): those of the value's kind first, at most two
        elsewhere = [(p, table) for p, table in paths if key in (p[-1], "_".join(p))]
        is_table = isinstance(value, dict)
        elsewhere.sort(key=lambda match: match[1] != is_table)
        if elsewhere:
            listed = " or ".join(_where_in(schema, p) for p, _ in elsewhere[:2])
            return f"{name(path, key)}: did you mean {listed}?"
        close = difflib.get_close_matches(key, list(known), n=1, cutoff=0.75)
        return f"{name(path, key)}: did you mean {close[0]}?" if close else name(path, key)

    problems = list(shapes)
    if unknown:
        listed = "; ".join(described(*entry) for entry in unknown)
        problems.append(f"unknown {'key' if len(unknown) == 1 else 'keys'}: {listed}")
    raise ConfigError(f"{where}: {'; '.join(problems)}")
