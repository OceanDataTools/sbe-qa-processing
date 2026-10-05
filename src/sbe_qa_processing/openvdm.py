"""Runs the QA from an OpenVDM post-transfer hook.

OpenVDM (https://github.com/OceanDataTools/openvdm) runs ``postCollectionSystemTransfer`` hook
commands from ``openvdm.yaml`` after each collection system transfer. Everything the report
needs comes from OpenVDM's ``api/warehouse/getCruiseConfig``: the cruise ID, name, PI, location,
dates and ports; the warehouse directory and MD5 summary file; and each collection system
transfer's and extra directory's destination. A cruise bounding box comes from the GeoJSON
tracklines OpenVDM's ``build_cruise_tracks`` writes, else from the optional site config.

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

from sbe_qa_processing.config import CruiseConfig, Extent, Port, Thresholds, _thresholds

logger = logging.getLogger(__name__)

DEFAULT_OPENVDM_CONFIG = Path("/opt/openvdm/server/etc/openvdm.yaml")
API_TIMEOUT = 30  # [s]
# A transfer is worth a new report only if it brought raw CTD files
CTD_FILE = re.compile(r"\.(hex|dat|xmlcon|con|hdr|bl)\b", re.IGNORECASE)


class OpenVDMError(RuntimeError):
    """OpenVDM's configuration doesn't support running the QA (e.g. a missing directory)"""


@dataclass
class SiteConfig:
    """Per-ship settings OpenVDM doesn't store, from an optional TOML::

    [vessel]
    id = "33RR"
    name = "Roger Revelle"

    [cruise]                  # R2R catalog ids, optional
    operator_id = "edu.ucsd.sio"
    scheduler_id = "org.unols"

    [extent]                  # used when there are no tracklines
    westernmost = -125.0
    easternmost = -115.0
    southernmost = 30.0
    northernmost = 36.0

    [openvdm]
    output_extra_directory = "CTD_QA"
    tracklines_extra_directory = "Tracklines"

    [provenance]
    distro_type = "post-cruise"

    [thresholds]              # as in the cruise TOML
    """

    vessel_id: str = ""
    vessel_name: str = ""
    operator_id: str = ""
    scheduler_id: str = ""
    extent: Extent | None = None
    output_extra_directory: str = "CTD_QA"
    tracklines_extra_directory: str = "Tracklines"
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
        tracklines_extra_directory=openvdm.get(
            "tracklines_extra_directory", defaults.tracklines_extra_directory
        ),
        distro_type=provenance.get("distro_type", defaults.distro_type),
        contact_institution=provenance.get("contact_institution", ""),
        contact_institution_id=provenance.get("contact_institution_id", ""),
        contact_email=provenance.get("contact_email", ""),
        thresholds=_thresholds(data.get("thresholds", {})),
    )


def site_root_from_config(openvdm_config: Path | str) -> str:
    """OpenVDM's web root (``siteRoot`` in openvdm.yaml), ending in a slash"""
    with open(openvdm_config) as file:
        site_root = yaml.safe_load(file)["siteRoot"]
    return site_root if site_root.endswith("/") else site_root + "/"


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


def _coordinates(geometry) -> list[tuple[float, float]]:
    """(longitude, latitude) pairs from any GeoJSON object"""
    if isinstance(geometry, dict):
        kind = geometry.get("type")
        if kind == "FeatureCollection":
            return [c for feature in geometry.get("features", []) for c in _coordinates(feature)]
        if kind == "Feature":
            return _coordinates(geometry.get("geometry"))
        if kind == "GeometryCollection":
            return [c for g in geometry.get("geometries", []) for c in _coordinates(g)]
        return _coordinates(geometry.get("coordinates"))
    if isinstance(geometry, list) and geometry:
        if isinstance(geometry[0], int | float):
            return [(float(geometry[0]), float(geometry[1]))] if len(geometry) >= 2 else []
        return [c for item in geometry for c in _coordinates(item)]
    return []


def tracklines_extent(directory: Path) -> Extent | None:
    """The bounding box of every GeoJSON trackline under directory, or None"""
    points = []
    for path in sorted(directory.rglob("*.geojson")) if directory.is_dir() else []:
        try:
            points += _coordinates(json.loads(path.read_text()))
        except (OSError, ValueError) as error:
            logger.warning("skipping unreadable trackline %s: %s", path, error)
    points = [(lon, lat) for lon, lat in points if -180 <= lon <= 180 and -90 <= lat <= 90]
    if not points:
        return None
    longitudes, latitudes = zip(*points, strict=True)
    return Extent(
        westernmost=min(longitudes),
        easternmost=max(longitudes),
        southernmost=min(latitudes),
        northernmost=max(latitudes),
    )


def _translate(path: str, cruise_id: str) -> str:
    return path.replace("{cruiseID}", cruise_id)


@dataclass
class HookRun:
    config: CruiseConfig
    fileset_dir: Path
    output_dir: Path
    owner: str  # the warehouse user, for file ownership
    extent_source: str  # "tracklines", "site config" or "none"


def resolve(
    cruise_config: dict, site: SiteConfig, transfer_name: str, fileset_id: str = ""
) -> HookRun:
    """The QA configuration and directories for a collection system transfer"""
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

    extent, extent_source = None, "none"
    tracks = extras.get(site.tracklines_extra_directory)
    if tracks is not None:
        extent = tracklines_extent(cruise_dir / _translate(tracks["destDir"], cruise_id))
        extent_source = "tracklines" if extent else "none"
    if extent is None and site.extent is not None:
        extent, extent_source = site.extent, "site config"

    md5_summary = warehouse.get("md5SummaryFn")
    depart = parse_openvdm_date(cruise_config.get("cruiseStartDate"))
    arrive = parse_openvdm_date(cruise_config.get("cruiseEndDate"))
    if depart is None:
        raise OpenVDMError("OpenVDM's cruise has no start date")
    config = CruiseConfig(
        cruise_id=cruise_id,
        depart_date=depart,
        # an open-ended cruise is still under way
        arrive_date=arrive or date.today(),  # noqa: DTZ011 - a calendar date
        extent=extent,
        fileset_id=fileset_id,
        cruise_name=cruise_config.get("cruiseName") or "",
        cruise_pi=cruise_config.get("cruisePI") or "",
        cruise_location=cruise_config.get("cruiseLocation") or "",
        vessel_id=site.vessel_id,
        vessel_name=site.vessel_name,
        operator_id=site.operator_id,
        scheduler_id=site.scheduler_id,
        depart_port=Port(name=cruise_config.get("cruiseStartPort") or ""),
        arrive_port=Port(name=cruise_config.get("cruiseEndPort") or ""),
        distro_type=site.distro_type,
        contact_institution=site.contact_institution,
        contact_institution_id=site.contact_institution_id,
        contact_email=site.contact_email,
        manifest_path=cruise_dir / md5_summary if md5_summary else None,
        thresholds=site.thresholds,
    )
    return HookRun(
        config=config,
        fileset_dir=fileset_dir,
        output_dir=output_dir,
        owner=warehouse.get("shipboardDataWarehouseUsername") or "",
        extent_source=extent_source,
    )


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
