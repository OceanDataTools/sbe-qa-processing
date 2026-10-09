"""Runs the QA from an OpenVDM post-transfer hook.

OpenVDM (https://github.com/OceanDataTools/openvdm) runs ``postCollectionSystemTransfer`` hook
commands from ``openvdm.yaml`` after each collection system transfer. Everything the report
needs comes from OpenVDM's ``api/warehouse/getCruiseConfig``: the cruise ID, name, PI, location,
dates and ports; the warehouse directory and MD5 summary file; and each collection system
transfer's and extra directory's destination; and, from OpenVDM 2.17, the cruise bounding box
(``cruiseExtent``) that ``build_cruise_tracks.py`` sets from the ship's track. Without it, the
bounding box comes from the optional site config.

OpenVDM 2.17 also keeps the vessel's name, contact and R2R IDs in ``openvdm.yaml``'s ``vessel``
block, which it copies into each cruise's ``ovdmConfig.json``. The hook reads it from
``openvdm.yaml`` when it has that file, else from the cruise's ``ovdmConfig.json``. Its values
win over the site config's.

The OpenVDM API is read over HTTP, so this runs in its own environment, not OpenVDM's venv.
"""

import json
import logging
import os
import re
import shutil
import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from sbe_qa_processing.config import (
    CruiseConfig,
    Extent,
    Port,
    Thresholds,
    _thresholds,
    _toml_value,
    config_to_toml,
    fields_of,
    thresholds_toml,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENVDM_CONFIG = Path("/opt/openvdm/server/etc/openvdm.yaml")
API_TIMEOUT = 30  # [s]
# A transfer is worth a new report only if it brought raw CTD files
CTD_FILE = re.compile(r"\.(hex|dat|xmlcon|con|hdr|bl)\b", re.IGNORECASE)


class OpenVDMError(RuntimeError):
    """OpenVDM's configuration doesn't support running the QA (e.g. a missing directory)"""


@dataclass
class SiteConfig:
    """Per-ship settings OpenVDM doesn't store, from an optional TOML. From OpenVDM 2.17, the
    vessel, R2R IDs and contact are in openvdm.yaml, whose values win over these::

    [vessel]
    id = "33RR"
    name = "Roger Revelle"

    [cruise]                  # R2R catalog ids, optional
    operator_id = "edu.ucsd.sio"
    scheduler_id = "org.unols"

    [extent]                  # used when OpenVDM has no cruiseExtent
    westernmost = -125.0
    easternmost = -115.0
    southernmost = 30.0
    northernmost = 36.0

    [openvdm]
    output_extra_directory = "CTD_QA"

    [provenance]
    distro_type = "post-cruise"
    contact_institution = "Scripps Institution of Oceanography"
    contact_institution_id = "edu.ucsd.sio"
    contact_email = "ship-tech@example.org"

    [thresholds]              # as in the cruise TOML
    """

    vessel_id: str = ""
    vessel_name: str = ""
    operator_id: str = ""
    scheduler_id: str = ""
    extent: Extent | None = None
    output_extra_directory: str = "CTD_QA"
    distro_type: str = "post-cruise"
    contact_institution: str = ""
    contact_institution_id: str = ""
    contact_email: str = ""
    thresholds: Thresholds = field(default_factory=Thresholds)


def load_site_config(path: Path | str | None) -> SiteConfig:
    if path is None:
        return SiteConfig()
    with open(path, "rb") as file:
        data = tomllib.load(file)
    vessel, cruise = data.get("vessel", {}), data.get("cruise", {})
    openvdm, provenance = data.get("openvdm", {}), data.get("provenance", {})
    if "tracklines_extra_directory" in openvdm:
        # The hook read GeoJSON tracklines before OpenVDM 2.17 stored the cruise extent
        logger.warning(
            "%s: [openvdm] tracklines_extra_directory is no longer used; the cruise extent "
            "comes from OpenVDM's cruiseExtent",
            path,
        )
    defaults = SiteConfig()
    return SiteConfig(
        vessel_id=vessel.get("id", ""),
        vessel_name=vessel.get("name", ""),
        operator_id=cruise.get("operator_id", ""),
        scheduler_id=cruise.get("scheduler_id", ""),
        extent=Extent(**data["extent"]) if "extent" in data else None,
        output_extra_directory=openvdm.get(
            "output_extra_directory", defaults.output_extra_directory
        ),
        distro_type=provenance.get("distro_type", defaults.distro_type),
        contact_institution=provenance.get("contact_institution", ""),
        contact_institution_id=provenance.get("contact_institution_id", ""),
        contact_email=provenance.get("contact_email", ""),
        thresholds=_thresholds(data.get("thresholds", {})),
    )


def site_config_to_toml(site: SiteConfig) -> str:
    """The site config as TOML that load_site_config reads back. Blank fields are written
    commented out, so the file shows everything that can be set
    """

    def entry(key: str, value, comment: str = "") -> str:
        line = f"{key} = {_toml_value(value)}" if value else f'# {key} = ""'
        return f"{line:<40} # {comment}" if comment else line

    lines = [
        "# Per-ship settings for the OpenVDM hook (sbe-qa-processing openvdm --site-config ...).",
        "# OpenVDM supplies the cruise ID, name, PI, location, dates and ports. From OpenVDM",
        "# 2.17, openvdm.yaml's vessel block supplies the vessel name, contact and R2R IDs,",
        "# and wins over the same fields here.",
        "",
        "[vessel]",
        entry("id", site.vessel_id, "R2R vessel ID (ICES code)"),
        entry("name", site.vessel_name),
        "",
        "[cruise]                                 # R2R catalog IDs",
        entry("operator_id", site.operator_id),
        entry("scheduler_id", site.scheduler_id),
        "",
        "# Cruise bounding box for the NAV tests, used when OpenVDM has no cruise extent",
        "# (OpenVDM 2.17's cruiseExtent, set by build_cruise_tracks.py). westernmost >",
        "# easternmost is a box across the antimeridian",
    ]
    edges = fields_of(Extent)
    if site.extent:
        lines += ["[extent]"] + [f"{k} = {getattr(site.extent, k)}" for k in edges]
    else:
        lines += ["# [extent]"] + [f"# {k} = 0.0" for k in edges]
    lines += [
        "",
        "[openvdm]",
        entry("output_extra_directory", site.output_extra_directory, "where reports go"),
        "",
        "[provenance]",
        entry("distro_type", site.distro_type),
        entry("contact_institution", site.contact_institution),
        entry("contact_institution_id", site.contact_institution_id, "R2R organization ID"),
        entry("contact_email", site.contact_email),
    ]
    thresholds = thresholds_toml(site.thresholds)
    if not thresholds:
        thresholds = ["", "# QA thresholds, as in a cruise TOML; e.g. for fresh water:"]
        thresholds += ["# [thresholds]", "# salinity_range = [0.0, 42.0]"]
    return "\n".join(lines + thresholds) + "\n"


def load_openvdm_yaml(openvdm_config: Path | str) -> dict:
    try:
        with open(openvdm_config) as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError:
        # e.g. OpenVDM installed outside /opt (its installer's INSTALL_ROOT)
        raise OpenVDMError(
            f"no openvdm.yaml at {openvdm_config}: give its path with --openvdm-config, or "
            "OpenVDM's web root URL with --site-root"
        ) from None


def site_root_from_config(openvdm_config: Path | str | dict) -> str:
    """OpenVDM's web root (``siteRoot`` in openvdm.yaml, or its loaded contents), ending in a
    slash
    """
    data = (
        openvdm_config if isinstance(openvdm_config, dict) else load_openvdm_yaml(openvdm_config)
    )
    site_root = data["siteRoot"]
    return site_root if site_root.endswith("/") else site_root + "/"


# OpenVDM 2.17's vessel block: the SiteConfig field each of its settings fills
VESSEL_SETTINGS = {
    "vessel_name": ("name",),
    "vessel_id": ("r2r", "vesselID"),
    "operator_id": ("r2r", "operatorID"),
    "scheduler_id": ("r2r", "schedulerID"),
    "contact_institution": ("contact", "institution"),
    "contact_email": ("contact", "email"),
}


@dataclass
class OpenVDMVessel:
    """The vessel settings OpenVDM has, by SiteConfig field, and where they came from"""

    settings: dict[str, str] = field(default_factory=dict)
    source: str = "none"  # "openvdm.yaml", "ovdmConfig.json" or "none"


def vessel_settings(block, source: str) -> OpenVDMVessel:
    """The set fields of a ``vessel`` block. Empty and missing values are unset; values are
    strings, so an R2R ID such as 3301 keeps its form
    """
    settings = {}
    for name, path in VESSEL_SETTINGS.items():
        value = block
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and not isinstance(value, dict) and str(value).strip():
            settings[name] = str(value).strip()
    return OpenVDMVessel(settings, source)


def cruise_vessel(cruise_dir: Path, config_filename: str) -> OpenVDMVessel:
    """The vessel block saved in the cruise's ovdmConfig.json, if any"""
    path = cruise_dir / config_filename
    try:
        block = json.loads(path.read_text()).get("vessel")
    except FileNotFoundError:
        return OpenVDMVessel()
    except (OSError, ValueError, AttributeError) as error:
        logger.warning("can't read the vessel settings from %s: %s", path, error)
        return OpenVDMVessel()
    return vessel_settings(block, "ovdmConfig.json") if block else OpenVDMVessel()


def fetch_cruise_config(site_root: str) -> dict:
    """The current cruise's configuration from OpenVDM's API"""
    url = f"{site_root}api/warehouse/getCruiseConfig"
    with urllib.request.urlopen(url, timeout=API_TIMEOUT) as response:
        return json.loads(response.read())


def parse_openvdm_date(text: str | None) -> date | None:
    """OpenVDM dates are 'YYYY/MM/DD HH:MM' (or just 'YYYY/MM/DD')"""
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None


def openvdm_extent(value) -> Extent | None:
    """OpenVDM's cruiseExtent: an object with the four edges [decimal degrees], or None when
    OpenVDM has none (no track yet, or OpenVDM before 2.17)
    """
    if not value:
        return None
    try:
        return Extent(**{edge: float(value[edge]) for edge in fields_of(Extent)})
    except (KeyError, TypeError, ValueError):
        logger.warning("ignoring OpenVDM's malformed cruiseExtent: %r", value)
        return None


def _translate(path: str, cruise_id: str) -> str:
    return path.replace("{cruiseID}", cruise_id)


@dataclass
class HookRun:
    config: CruiseConfig
    transfer: str
    fileset_dir: Path
    output_dir: Path
    owner: str  # the warehouse user, for file ownership
    extent_source: str  # "openvdm", "site config" or "none"
    vessel_source: str  # where OpenVDM's vessel block came from: "openvdm.yaml",
    # "ovdmConfig.json" or "none"
    # What the config is missing, or had to assume
    notes: list[str] = field(default_factory=list)


def resolve(
    cruise_config: dict,
    site: SiteConfig,
    transfer_name: str,
    fileset_id: str = "",
    vessel: OpenVDMVessel | None = None,
) -> HookRun:
    """The QA configuration and directories for a collection system transfer. vessel is
    openvdm.yaml's vessel block; without it, the one in the cruise's ovdmConfig.json is used
    """
    warehouse = cruise_config["warehouseConfig"]
    cruise_id = cruise_config["cruiseID"]
    if not cruise_id:
        raise OpenVDMError("OpenVDM has no current cruise")
    cruise_dir = Path(warehouse["shipboardDataWarehouseBaseDir"]) / cruise_id

    transfers = {t["name"]: t for t in cruise_config.get("collectionSystemTransfersConfig", [])}
    if transfer_name not in transfers:
        raise OpenVDMError(f"no collection system transfer named {transfer_name!r}")
    fileset_dir = cruise_dir / _translate(transfers[transfer_name]["destDir"], cruise_id)

    extras = {d["name"]: d for d in cruise_config.get("extraDirectoriesConfig", [])}
    output = extras.get(site.output_extra_directory)
    if output is None:
        raise OpenVDMError(
            f"no extra directory named {site.output_extra_directory!r}: add it in OpenVDM "
            "(Configuration > Extra Directories) or set [openvdm] output_extra_directory"
        )
    output_dir = cruise_dir / _translate(output["destDir"], cruise_id)

    notes = []
    extent, extent_source = openvdm_extent(cruise_config.get("cruiseExtent")), "openvdm"
    if extent is None and site.extent is not None:
        extent, extent_source = site.extent, "site config"
        notes.append("OpenVDM has no cruise extent; using the site config's [extent]")
    elif extent is None:
        extent_source = "none"
        notes.append(
            "no cruise extent: OpenVDM has none (it needs OpenVDM 2.17 and build_cruise_tracks.py) "
            "and the site config has no [extent], so the Lat/Lon test is GREY (N)"
        )

    md5_summary = warehouse.get("md5SummaryFn")
    depart = parse_openvdm_date(cruise_config.get("cruiseStartDate"))
    arrive = parse_openvdm_date(cruise_config.get("cruiseEndDate"))
    if depart is None:
        raise OpenVDMError("OpenVDM's cruise has no start date")
    if arrive is None:
        # an open-ended cruise is still under way
        arrive = date.today()  # noqa: DTZ011 - a calendar date
        notes.append(
            f"OpenVDM's cruise has no end date; arrive_date is today ({arrive}), so casts after "
            "today fail the date test unless the cruise TOML is updated"
        )
    # Port R2R IDs, once OpenVDM stores them (OceanDataTools/openvdm#364)
    depart_port = Port(
        name=cruise_config.get("cruiseStartPort") or "",
        port_id=str(cruise_config.get("cruiseStartPortID") or ""),
    )
    arrive_port = Port(
        name=cruise_config.get("cruiseEndPort") or "",
        port_id=str(cruise_config.get("cruiseEndPortID") or ""),
    )
    if not (depart_port.port_id and arrive_port.port_id):
        notes.append("OpenVDM has no R2R port IDs, so the XML's port IDs are blank")
    if vessel is None:
        config_filename = warehouse.get("cruiseConfigFn") or "ovdmConfig.json"
        vessel = cruise_vessel(cruise_dir, config_filename)
    # OpenVDM's vessel settings win over the site config's, field by field
    ship = {}
    for name in VESSEL_SETTINGS:
        theirs, ours = vessel.settings.get(name, ""), getattr(site, name)
        ship[name] = theirs or ours
        if theirs and ours and theirs != ours:
            notes.append(
                f"{name} is {theirs!r} in OpenVDM's {vessel.source} and {ours!r} in the site "
                f"config; using {vessel.source}'s"
            )
    if not ship["vessel_id"]:
        notes.append(
            "no vessel ID: set vessel.r2r.vesselID in openvdm.yaml (OpenVDM 2.17), or [vessel] "
            "id in the site config"
        )
    config = CruiseConfig(
        cruise_id=cruise_id,
        depart_date=depart,
        arrive_date=arrive,
        extent=extent,
        fileset_id=fileset_id,
        cruise_name=cruise_config.get("cruiseName") or "",
        cruise_pi=cruise_config.get("cruisePI") or "",
        cruise_location=cruise_config.get("cruiseLocation") or "",
        vessel_id=ship["vessel_id"],
        vessel_name=ship["vessel_name"],
        operator_id=ship["operator_id"],
        scheduler_id=ship["scheduler_id"],
        depart_port=depart_port,
        arrive_port=arrive_port,
        distro_type=site.distro_type,
        contact_institution=ship["contact_institution"],
        # On board, the report comes from the ship's operator, so OpenVDM doesn't store this
        contact_institution_id=site.contact_institution_id or ship["operator_id"],
        contact_email=ship["contact_email"],
        manifest_path=cruise_dir / md5_summary if md5_summary else None,
        thresholds=site.thresholds,
    )
    return HookRun(
        config=config,
        transfer=transfer_name,
        fileset_dir=fileset_dir,
        output_dir=output_dir,
        owner=warehouse.get("shipboardDataWarehouseUsername") or "",
        extent_source=extent_source,
        vessel_source=vessel.source,
        notes=notes,
    )


def cruise_toml(run: HookRun, site_root: str) -> str:
    """The resolved cruise TOML, headed by where it came from and what it's missing"""
    header = [f"# Resolved from OpenVDM ({site_root}) for {run.transfer}"]
    header += [f"# Note: {note}" for note in run.notes]
    return "\n".join(header) + "\n" + config_to_toml(run.config)


def has_ctd_changes(changed: list[str]) -> bool:
    """Whether OpenVDM's {newFiles}/{updatedFiles} include raw CTD files. File names arrive
    space-joined (and may themselves contain spaces), so this matches extensions in the text
    """
    return any(CTD_FILE.search(text) for text in changed)


def chown_tree(path: Path, owner: str) -> None:
    """Give the reports to the warehouse user, as OpenVDM does, when running as root"""
    if not owner or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    for item in [path, *path.rglob("*")]:
        try:
            shutil.chown(item, user=owner, group=owner)
        except (LookupError, OSError) as error:
            logger.warning("could not chown %s to %s: %s", item, owner, error)
            return
