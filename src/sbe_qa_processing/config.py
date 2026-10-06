"""Cruise configuration: the metadata R2R normally takes from its catalog, plus QA thresholds.

A cruise TOML looks like::

    [cruise]
    id = "SP2613"
    name = "..."
    vessel_id = "32ST"
    vessel_name = "Sproul"
    operator_id = "edu.ucsd.sio"
    depart_date = 2026-07-09
    arrive_date = 2026-07-09

    [cruise.extent]
    westernmost = -117.38
    easternmost = -117.22
    southernmost = 32.59
    northernmost = 32.71

    [fileset]
    id = 169847

    [thresholds]            # optional, defaults below
    temperature_difference = 0.01
"""

import json
import tomllib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

R2R_NAMESPACE = "https://service.rvdata.us/schema/r2r-2.0"
R2R_API = "https://service.rvdata.us/api"
R2R_API_TIMEOUT = 60  # [s]


@dataclass
class Port:
    name: str = ""
    port_id: str = ""
    country_id: str = ""
    state_id: str = ""
    longitude: float | None = None
    latitude: float | None = None


@dataclass
class Extent:
    """Cruise bounding box in decimal degrees (longitude east positive)"""

    westernmost: float
    easternmost: float
    southernmost: float
    northernmost: float

    def contains(self, latitude: float, longitude: float) -> bool:
        return (
            self.southernmost <= latitude <= self.northernmost
            and self.westernmost <= longitude <= self.easternmost
        )


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
    # [%] R2R-style tests: a test is YELLOW at or above this pass percentage, RED below
    yellow_minimum_percent: float = 50.0


@dataclass
class CruiseConfig:
    cruise_id: str
    depart_date: date
    arrive_date: date
    # None when no bounding box is known; the NAV location test is then GREY (N)
    extent: Extent | None = None
    # R2R's fileset ID, assigned after submission; optional
    fileset_id: str = ""
    cruise_name: str = ""
    cruise_pi: str = ""
    cruise_location: str = ""
    vessel_id: str = ""
    vessel_name: str = ""
    operator_id: str = ""
    scheduler_id: str = ""
    depart_port: Port = field(default_factory=Port)
    arrive_port: Port = field(default_factory=Port)
    distro_type: str = "post-cruise"
    contact_institution: str = ""
    contact_institution_id: str = ""
    contact_email: str = ""
    # Checksum manifest for a plain directory (e.g. OpenVDM's MD5 summary): "<md5> <path>" lines,
    # with paths relative to the manifest's directory. A BagIt bag uses its own manifest-md5.txt
    manifest_path: Path | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def identifier(self) -> str:
        """<cruise>_<fileset>, or <cruise>_ctd without an R2R fileset ID"""
        return f"{self.cruise_id}_{self.fileset_id or 'ctd'}"


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _thresholds(table: dict) -> Thresholds:
    known = {f.name for f in fields(Thresholds)}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"unknown thresholds: {', '.join(sorted(unknown))}")
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
    return Thresholds(**values)


def load_config(path: Path | str) -> CruiseConfig:
    with open(path, "rb") as file:
        data = tomllib.load(file)
    cruise = data["cruise"]
    provenance = data.get("provenance", {})
    fileset = data.get("fileset", {})
    manifest = fileset.get("manifest")
    return CruiseConfig(
        cruise_id=str(cruise["id"]),
        fileset_id=str(fileset.get("id", "")),
        depart_date=_as_date(cruise["depart_date"]),
        arrive_date=_as_date(cruise["arrive_date"]),
        extent=Extent(**cruise["extent"]) if "extent" in cruise else None,
        cruise_name=cruise.get("name", ""),
        cruise_pi=cruise.get("pi", ""),
        cruise_location=cruise.get("location", ""),
        vessel_id=cruise.get("vessel_id", ""),
        vessel_name=cruise.get("vessel_name", ""),
        operator_id=cruise.get("operator_id", ""),
        scheduler_id=cruise.get("scheduler_id", ""),
        depart_port=Port(**cruise.get("depart_port", {})),
        arrive_port=Port(**cruise.get("arrive_port", {})),
        distro_type=provenance.get("distro_type", "post-cruise"),
        contact_institution=provenance.get("contact_institution", ""),
        contact_institution_id=provenance.get("contact_institution_id", ""),
        contact_email=provenance.get("contact_email", ""),
        manifest_path=(Path(path).parent / manifest).resolve() if manifest else None,
        thresholds=_thresholds(data.get("thresholds", {})),
    )


def config_to_toml(config: CruiseConfig) -> str:
    """The config as cruise TOML, e.g. to snapshot what an OpenVDM hook resolved"""
    lines = ["[cruise]"]
    for key, value in (
        ("id", config.cruise_id),
        ("name", config.cruise_name),
        ("pi", config.cruise_pi),
        ("location", config.cruise_location),
        ("vessel_id", config.vessel_id),
        ("vessel_name", config.vessel_name),
        ("operator_id", config.operator_id),
        ("scheduler_id", config.scheduler_id),
    ):
        if value:
            lines.append(f"{key} = {_toml_value(value)}")
    lines += [f"depart_date = {config.depart_date}", f"arrive_date = {config.arrive_date}"]
    if config.extent:
        lines += ["", "[cruise.extent]"]
        lines += [f"{k} = {getattr(config.extent, k)}" for k in fields_of(Extent)]
    for which in ("depart_port", "arrive_port"):
        port = getattr(config, which)
        values = {
            k: getattr(port, k) for k in fields_of(Port) if getattr(port, k) not in ("", None)
        }
        if values:
            lines += ["", f"[cruise.{which}]"] + [
                f"{k} = {_toml_value(v)}" for k, v in values.items()
            ]
    fileset = {}
    if config.fileset_id:
        fileset["id"] = config.fileset_id
    if config.manifest_path:
        fileset["manifest"] = str(config.manifest_path)
    if fileset:
        lines += ["", "[fileset]"] + [f"{k} = {_toml_value(v)}" for k, v in fileset.items()]
    provenance = {
        k: getattr(config, k)
        for k in ("distro_type", "contact_institution", "contact_institution_id", "contact_email")
        if getattr(config, k)
    }
    lines += ["", "[provenance]"] + [f"{k} = {_toml_value(v)}" for k, v in provenance.items()]
    lines += thresholds_toml(config.thresholds)
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


def _toml_value(value) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)


def fetch_r2r_records(endpoint: str, cruise_id: str) -> list[dict]:
    """A cruise's records from R2R's catalog API (``api/cruise``, ``api/fileset``). The API
    answers "not found" with a 204 status in the body and no data
    """
    url = f"{R2R_API}/{endpoint}/?cruise_id={urllib.parse.quote(cruise_id)}"
    with urllib.request.urlopen(url, timeout=R2R_API_TIMEOUT) as response:
        return json.loads(response.read()).get("data") or []


def config_from_r2r_api(cruise: dict, fileset: dict | None = None) -> CruiseConfig:
    """Cruise config from R2R's catalog API: a record from ``api/cruise/?cruise_id=...`` and
    optionally one from ``api/fileset/?cruise_id=...``.

    R2R's QA reports identify the vessel by its ICES code, which the API calls
    ``vessel_ices_code`` (its ``vessel_id`` is the vessel's name). The API has no port
    coordinates, country or state, so ports carry only their name and R2R ID.
    """
    value = lambda key: str(cruise.get(key) or "").strip()
    edges = ("longitude_min", "longitude_max", "latitude_min", "latitude_max")
    extent = None
    if all(value(edge) for edge in edges):
        west, east, south, north = (float(value(edge)) for edge in edges)
        extent = Extent(westernmost=west, easternmost=east, southernmost=south, northernmost=north)
    return CruiseConfig(
        cruise_id=value("cruise_id"),
        fileset_id=str(fileset["fileset_id"]) if fileset else "",
        depart_date=_as_date(value("depart_date")),
        arrive_date=_as_date(value("arrive_date")),
        extent=extent,
        cruise_name=value("cruise_name"),
        cruise_pi=value("chief_scientist"),
        cruise_location=value("waterbody_name"),
        vessel_id=value("vessel_ices_code") or value("vessel_id"),
        vessel_name=value("vessel_shortname") or value("vessel_name"),
        operator_id=value("operator_id"),
        scheduler_id=value("scheduler_id"),
        depart_port=Port(name=value("depart_port_name"), port_id=value("depart_port_id")),
        arrive_port=Port(name=value("arrive_port_name"), port_id=value("arrive_port_id")),
    )


def config_toml_from_r2r_qa(qa_xml: Path | str) -> str:
    """Cruise TOML text built from an existing R2R QA report's filesetinfo, for checking this
    tool's results against R2R's
    """
    ns = {"r2r": R2R_NAMESPACE}
    root = ET.parse(qa_xml).getroot()
    cruise = root.find(".//r2r:filesetinfo/r2r:cruise", ns)
    text = lambda element, tag: (element.findtext(f"r2r:{tag}", "", ns) or "").strip()
    lines = [
        f"# Generated from {Path(qa_xml).name}",
        "[cruise]",
        f"id = {_toml_value(text(cruise, 'id'))}",
        f"name = {_toml_value(text(cruise, 'cname'))}",
        f"vessel_id = {_toml_value(text(cruise, 'vessel_id'))}",
        f"operator_id = {_toml_value(text(cruise, 'operator_id'))}",
        f"scheduler_id = {_toml_value(text(cruise, 'scheduler_id'))}",
        f"depart_date = {text(cruise, 'depart_date')}",
        f"arrive_date = {text(cruise, 'arrive_date')}",
        "",
        "[cruise.extent]",
    ]
    extent = cruise.find("r2r:extent", ns)
    for edge in ("westernmost", "easternmost", "southernmost", "northernmost"):
        lines.append(f"{edge} = {float(text(extent, edge))}")
    for which in ("depart_port", "arrive_port"):
        port = cruise.find(f"r2r:{which}", ns)
        if port is None:
            continue
        lines += ["", f"[cruise.{which}]"]
        lines.append(f"name = {_toml_value(text(port, 'port_name'))}")
        lines.append(f"port_id = {_toml_value(text(port, 'port_id'))}")
        lines.append(f"country_id = {_toml_value(text(port, 'country_id'))}")
        lines.append(f"state_id = {_toml_value(text(port, 'state_id'))}")
        for coordinate in ("longitude", "latitude"):
            if text(port, coordinate):
                lines.append(f"{coordinate} = {float(text(port, coordinate))}")
    provenance = root.find("r2r:provenance", ns)
    lines += [
        "",
        "[fileset]",
        f"id = {text(root.find('.//r2r:filesetinfo/r2r:fileset', ns), 'id')}",
        "",
        "[provenance]",
        f"distro_type = {_toml_value(text(provenance, 'distro_type'))}",
    ]
    return "\n".join(lines) + "\n"
