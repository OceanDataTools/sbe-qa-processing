"""Cruise configs from R2R's catalog API and from R2R's QA 2.0 reports."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from sbe_qa_processing.config import Extent, as_date, toml_value
from sbe_qa_processing.integrations.r2r.config import CruiseConfig, Port
from sbe_qa_processing.network import reaching

R2R_NAMESPACE = "https://service.rvdata.us/schema/r2r-2.0"
R2R_API = "https://service.rvdata.us/api"
R2R_API_TIMEOUT = 60  # [s]


class R2RError(LookupError):
    """R2R's catalog doesn't have what was asked for, or R2R answered with an HTTP error.
    `status` is that error's HTTP status, else None
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def http_error(error: urllib.error.HTTPError, what: str, url: str) -> R2RError:
    """An HTTP error from R2R as a one-line R2RError naming what was being fetched"""
    host = urllib.parse.urlsplit(url).hostname or url
    retry = "; try again later" if error.code >= 500 else ""
    return R2RError(
        f"{what} ({host}) answered {error.code} {error.reason}{retry}", status=error.code
    )


def fetch_r2r_records(endpoint: str, **query) -> list[dict]:
    """Records from R2R's catalog API (``api/cruise``, ``api/fileset``, ``api/qa_info``) for a
    query, e.g. ``cruise_id=...``. The API answers "not found" with a 204 status in the body and
    no data
    """
    url = f"{R2R_API}/{endpoint}/?{urllib.parse.urlencode(query)}"
    try:
        with (
            reaching("R2R", url),
            urllib.request.urlopen(url, timeout=R2R_API_TIMEOUT) as response,
        ):
            body = response.read()
    except urllib.error.HTTPError as error:
        raise http_error(error, "R2R's catalog", url) from error
    return json.loads(body).get("data") or []


# R2R's QA reports among a fileset's documents: the QA 2.0 report, or an older one
QA_REPORT = re.compile(r"_qa(\.2\.0)?\.xml$")


def qa_report_url(fileset_id: int | str) -> str | None:
    """The URL of R2R's QA report for a fileset, from ``api/qa_info``'s documents (the QA 2.0
    report ahead of an older one), or None when R2R lists none
    """
    urls = [
        document["uri"]
        for record in fetch_r2r_records("qa_info", fileset_id=fileset_id)
        for document in json.loads(record.get("documents") or "[]")
        if QA_REPORT.search(document.get("file_name", "")) and document.get("uri")
    ]
    urls.sort(key=lambda url: not url.endswith("_qa.2.0.xml"))
    return urls[0] if urls else None


def config_from_r2r_catalog(cruise_id: str, fileset_id: int) -> CruiseConfig:
    """Cruise config from R2R's catalog API, checking the CTD fileset belongs to the cruise"""
    cruises = fetch_r2r_records("cruise", cruise_id=cruise_id)
    if not cruises:
        raise R2RError(f"R2R has no cruise {cruise_id}")
    filesets = fetch_r2r_records("fileset", cruise_id=cruise_id)
    ctd = [f for f in filesets if f.get("device_type") == "ctd"]
    matches = [f for f in ctd if f.get("fileset_id") == fileset_id]
    if not matches:
        others = [f for f in filesets if f.get("fileset_id") == fileset_id]
        problem = (
            f"fileset {fileset_id} is {others[0].get('label')}"
            if others
            else f"has no fileset {fileset_id}"
        )
        listed = ", ".join(f"{f['fileset_id']} ({f.get('label')})" for f in ctd) or "none"
        raise R2RError(f"{cruise_id} {problem}; its CTD filesets: {listed}")
    return config_from_r2r_api(cruises[0], matches[0])


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
        depart_date=as_date(value("depart_date")),
        arrive_date=as_date(value("arrive_date")),
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
        f"id = {toml_value(text(cruise, 'id'))}",
        f"name = {toml_value(text(cruise, 'cname'))}",
        f"vessel_id = {toml_value(text(cruise, 'vessel_id'))}",
        f"operator_id = {toml_value(text(cruise, 'operator_id'))}",
        f"scheduler_id = {toml_value(text(cruise, 'scheduler_id'))}",
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
        lines.append(f"name = {toml_value(text(port, 'port_name'))}")
        lines.append(f"port_id = {toml_value(text(port, 'port_id'))}")
        lines.append(f"country_id = {toml_value(text(port, 'country_id'))}")
        lines.append(f"state_id = {toml_value(text(port, 'state_id'))}")
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
        f"distro_type = {toml_value(text(provenance, 'distro_type'))}",
    ]
    return "\n".join(lines) + "\n"
