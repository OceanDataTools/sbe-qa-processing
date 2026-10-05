"""Writes the R2R QA 2.0 XML rollup report.

Element order, namespaces and the stylesheet reference mirror R2R's own reports (e.g.
AE2608_169213_qa.2.0.xml), so R2R's XSLT renders the file. The catalog sections R2R fills from
its database (filesetinfo) come from the cruise config instead.
"""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing.config import R2R_NAMESPACE, Port
from sbe_qa_processing.qa import QAResult

STYLESHEET = "https://service.rvdata.us/stylesheets/r2r_qac_v2.0.xslt"
# R2R's reports declare these exact values, including the https XSI namespace
XSI_NAMESPACE = "https://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = "http://schema.rvdata.us/2.0 http://schema.rvdata.us/2.0/base.xsd"

ET.register_namespace("r2r", R2R_NAMESPACE)
ET.register_namespace("xsi", XSI_NAMESPACE)


def _tag(name: str) -> str:
    return f"{{{R2R_NAMESPACE}}}{name}"


def _sub(parent: ET.Element, tag: str, text=None, /, **attributes) -> ET.Element:
    element = ET.SubElement(parent, _tag(tag), {k: str(v) for k, v in attributes.items()})
    if text is not None:
        element.text = str(text)
    return element


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _port(parent: ET.Element, name: str, port: Port) -> None:
    element = _sub(parent, name)
    _sub(element, "port_id", port.port_id)
    _sub(element, "port_name", port.name)
    _sub(element, "country_id", port.country_id)
    _sub(element, "state_id", port.state_id)
    _sub(element, "longitude", "" if port.longitude is None else port.longitude)
    _sub(element, "latitude", "" if port.latitude is None else port.latitude)


def build_qa_xml(result: QAResult) -> ET.ElementTree:
    config = result.config
    root = ET.Element(_tag("qareport"), {f"{{{XSI_NAMESPACE}}}schemaLocation": SCHEMA_LOCATION})
    root.append(
        ET.Comment("Rolling Deck to Repository (R2R) Quality Assessment (QA) Rollup Report")
    )
    _sub(root, "version", "2.0")
    _sub(root, "identifier", f"{config.identifier}_qa")

    provenance = _sub(root, "provenance")
    institution = _sub(provenance, "contact_institution", config.contact_institution)
    if config.contact_institution_id:
        institution.set("id", config.contact_institution_id)
    _sub(provenance, "contact_email", config.contact_email)
    _sub(provenance, "distro_type", config.distro_type)
    _sub(provenance, "cruise_id", config.cruise_id)
    _sub(provenance, "fileset_id", config.fileset_id)
    _sub(provenance, "device_type", "ctd")
    updates = _sub(provenance, "updates")
    update = _sub(updates, "update", description="Quality Assessment (QA)")
    _sub(update, "process", "sbe-qa-processing", version=result.versions["sbe-qa-processing"])
    _sub(update, "time", _timestamp(result.finished))
    conversion = _sub(updates, "update", description="Raw data conversion")
    _sub(conversion, "process", "seabirdscientific", version=result.versions["seabirdscientific"])
    _sub(conversion, "time", _timestamp(result.finished))

    filesetinfo = _sub(root, "filesetinfo")
    _sub(filesetinfo, "namespace", "R2R")
    cruise = _sub(filesetinfo, "cruise")
    _sub(cruise, "id", config.cruise_id)
    _sub(cruise, "cname", config.cruise_name)
    _sub(cruise, "operator_id", config.operator_id)
    _sub(cruise, "vessel_id", config.vessel_id)
    _sub(cruise, "scheduler_id", config.scheduler_id)
    _sub(cruise, "depart_date", config.depart_date.isoformat())
    _port(cruise, "depart_port", config.depart_port)
    _sub(cruise, "arrive_date", config.arrive_date.isoformat())
    _port(cruise, "arrive_port", config.arrive_port)
    extent = _sub(cruise, "extent")
    for edge in ("westernmost", "easternmost", "southernmost", "northernmost"):
        _sub(extent, edge, getattr(config.extent, edge) if config.extent else None)
    fileset = _sub(filesetinfo, "fileset")
    _sub(fileset, "type", "data")
    _sub(fileset, "id", config.fileset_id)
    _sub(fileset, "vessel_id", config.vessel_id)
    _sub(fileset, "cruise_id", config.cruise_id)
    device = _sub(fileset, "device")
    _sub(device, "devicetype_id", "ctd")
    _sub(device, "make_id", "com.seabird")
    _sub(device, "model_name", "SBE-911+")
    _sub(fileset, "fileformat_alias", "seasoft-raw")
    _sub(fileset, "proclevel", "1")

    certificate = _sub(root, "certificate")
    _sub(certificate, "rating", result.r2r.rating, description=result.r2r.description)
    tests = _sub(certificate, "tests")
    for test in result.r2r.tests:
        element = _sub(tests, "test", description=test.description, name=test.name)
        _sub(element, "rating", test.rating)
        if test.result is not None:
            _sub(element, "test_result", test.result, uom=test.uom)
        bounds = _sub(element, "bounds")
        for bound in test.bounds:
            _sub(bounds, "bound", bound.value, name=bound.name, uom=bound.uom)
    infos = _sub(certificate, "infos")
    for info in result.r2r.infos:
        _sub(infos, "info", info.value, name=info.name, uom=info.uom)

    _sub(root, "filesetinfo_supplements")
    _sub(root, "references")
    log = _sub(root, "log")
    for name in result.r2r.checksum_failures:
        _sub(log, "entry", f"checksum mismatch or missing file: {name}")
    for cast in result.casts:
        for stage, message in cast.errors.items():
            _sub(log, "entry", f"{cast.name}: {stage}: {message}")
    _sub(root, "configuration")

    manifest = _sub(root, "manifest")
    files = _sub(manifest, "files")
    for entry in result.fileset.manifest:
        _sub(files, "file", entry.path, id=entry.file_id, checksum=entry.md5, order="")

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def write_qa_xml(result: QAResult, path: Path | str) -> Path:
    path = Path(path)
    tree = build_qa_xml(result)
    body = ET.tostring(tree.getroot(), encoding="unicode")
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        f'<?xml-stylesheet type="text/xsl" href="{STYLESHEET}"?>\n' + body + "\n",
        encoding="utf-8",
    )
    return path
