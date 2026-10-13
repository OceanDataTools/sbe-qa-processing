"""R2R's cruise config: the core QAConfig plus R2R's IDs, ports, fileset ID and provenance.

The cruise TOML is a core config TOML with these added::

    [cruise]
    id = "SP2613"              # required, as are the dates
    vessel_id = "32QU"         # R2R's vessel ID, the ICES code
    operator_id = "edu.ucsd.sio"
    scheduler_id = "org.unols"
    depart_date = 2026-07-09
    arrive_date = 2026-07-09

    [cruise.depart_port]       # and [cruise.arrive_port]
    name = "San Diego"
    port_id = "100055"

    [fileset]
    id = 169847

    [provenance]
    distro_type = "post-cruise"
    contact_institution_id = "edu.ucsd.sio"

    [thresholds]
    yellow_minimum_percent = 50.0
"""

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sbe_qa_processing.config import (
    Extent,
    QAConfig,
    fields_of,
    load_config,
    thresholds_toml,
    toml_value,
)
from sbe_qa_processing.theme import theme_toml


@dataclass
class Port:
    name: str = ""
    port_id: str = ""
    country_id: str = ""
    state_id: str = ""
    longitude: float | None = None
    latitude: float | None = None


# [%] R2R's NAV tests are YELLOW at or above this pass percentage, RED below
YELLOW_MINIMUM_PERCENT = 50.0
# [thresholds] key for it in a cruise TOML; R2R's, so not one of the Thresholds
YELLOW_MINIMUM_KEY = "yellow_minimum_percent"


@dataclass(kw_only=True)
class CruiseConfig(QAConfig):
    """A cruise as R2R catalogs it: the core config plus R2R's IDs, ports and provenance, with
    the cruise ID and dates required. The R2R tests and XML need it
    """

    cruise_id: str
    depart_date: date
    arrive_date: date
    # R2R's fileset ID, assigned after submission; optional
    fileset_id: str = ""
    vessel_id: str = ""
    operator_id: str = ""
    scheduler_id: str = ""
    depart_port: Port = field(default_factory=Port)
    arrive_port: Port = field(default_factory=Port)
    distro_type: str = "post-cruise"
    contact_institution_id: str = ""
    yellow_minimum_percent: float = YELLOW_MINIMUM_PERCENT

    @property
    def identifier(self) -> str:
        """<cruise>_<fileset>, or <cruise>_ctd without an R2R fileset ID"""
        return f"{self.cruise_id}_{self.fileset_id or 'ctd'}"

    def __str__(self) -> str:
        """The cruise TOML, as load_cruise_config reads it"""
        return config_to_toml(self).rstrip("\n")


# What a CruiseConfig needs, from [cruise]
REQUIRED = ("id", "depart_date", "arrive_date")


def load_cruise_config(path: Path | str) -> CruiseConfig:
    """A cruise TOML, with the cruise ID and dates R2R needs"""
    with open(path, "rb") as file:
        data = tomllib.load(file)
    cruise = data.get("cruise", {})
    missing = [key for key in REQUIRED if key not in cruise]
    if missing:
        raise ValueError(f"{path}: R2R needs [cruise] {', '.join(missing)}")
    core = load_config(path)
    provenance = data.get("provenance", {})
    return CruiseConfig(
        **{name: getattr(core, name) for name in fields_of(QAConfig)},
        fileset_id=str(data.get("fileset", {}).get("id", "")),
        vessel_id=cruise.get("vessel_id", ""),
        operator_id=cruise.get("operator_id", ""),
        scheduler_id=cruise.get("scheduler_id", ""),
        depart_port=Port(**cruise.get("depart_port", {})),
        arrive_port=Port(**cruise.get("arrive_port", {})),
        distro_type=provenance.get("distro_type", "post-cruise"),
        contact_institution_id=provenance.get("contact_institution_id", ""),
        yellow_minimum_percent=data.get("thresholds", {}).get(
            YELLOW_MINIMUM_KEY, YELLOW_MINIMUM_PERCENT
        ),
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
            lines.append(f"{key} = {toml_value(value)}")
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
                f"{k} = {toml_value(v)}" for k, v in values.items()
            ]
    fileset = {}
    if config.fileset_id:
        fileset["id"] = config.fileset_id
    if config.manifest_path:
        fileset["manifest"] = str(config.manifest_path)
    if fileset:
        lines += ["", "[fileset]"] + [f"{k} = {toml_value(v)}" for k, v in fileset.items()]
    provenance = {
        k: getattr(config, k)
        for k in ("distro_type", "contact_institution", "contact_institution_id", "contact_email")
        if getattr(config, k)
    }
    lines += ["", "[provenance]"] + [f"{k} = {toml_value(v)}" for k, v in provenance.items()]
    thresholds = thresholds_toml(config.thresholds)
    if config.yellow_minimum_percent != YELLOW_MINIMUM_PERCENT:
        thresholds = (thresholds or ["", "[thresholds]"]) + [
            f"{YELLOW_MINIMUM_KEY} = {config.yellow_minimum_percent}"
        ]
    lines += thresholds
    lines += theme_toml(config.theme)
    return "\n".join(lines) + "\n"
