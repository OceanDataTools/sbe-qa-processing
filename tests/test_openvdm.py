"""The OpenVDM hook, against a stand-in OpenVDM API and an OpenVDM-style cruise directory."""

import json
import os
import shutil
import threading
import xml.etree.ElementTree as ET
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from conftest import r2r_fileset

from sbe_qa_processing import openvdm
from sbe_qa_processing.cli import main
from sbe_qa_processing.config import R2R_NAMESPACE, Extent
from sbe_qa_processing.fileset import load_fileset, md5sum
from sbe_qa_processing.r2r import checksum_test

NS = {"r2r": R2R_NAMESPACE}


def cruise_config(base_dir: Path, cruise_id="SP2613") -> dict:
    """A getCruiseConfig response, trimmed to what the hook reads"""
    return {
        "cruiseID": cruise_id,
        "cruiseName": "Sproul engineering cruise",
        "cruisePI": "A. Scientist",
        "cruiseLocation": "San Diego Trough",
        "cruiseStartDate": "2026/07/09 00:00",
        "cruiseEndDate": "2026/07/10 00:00",
        "cruiseStartPort": "San Diego, CA",
        "cruiseEndPort": "San Diego, CA",
        "warehouseConfig": {
            "shipboardDataWarehouseBaseDir": str(base_dir),
            "shipboardDataWarehouseUsername": "survey",
            "md5SummaryFn": "MD5_Summary.txt",
        },
        "collectionSystemTransfersConfig": [
            {"collectionSystemTransferID": "4", "name": "CTD", "destDir": "CTD"},
            {"collectionSystemTransferID": "5", "name": "SCS", "destDir": "SCS"},
        ],
        "extraDirectoriesConfig": [
            {"name": "Dashboard_Data", "destDir": "OpenVDM/DashboardData", "enable": "1"},
            {"name": "CTD_QA", "destDir": "Products/CTD_QA", "enable": "1"},
            {"name": "Tracklines", "destDir": "OpenVDM/Tracklines", "enable": "1"},
        ],
    }


def trackline(path: Path, points: list[tuple[float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    feature = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": points}}
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))


# Unit behavior ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("2026/07/09 14:30", date(2026, 7, 9)), ("2026/07/09", date(2026, 7, 9)), ("", None)],
)
def test_parse_openvdm_date(text, expected):
    assert openvdm.parse_openvdm_date(text) == expected


@pytest.mark.parametrize(
    "changed, expected",
    [
        ([""], False),
        (["SCS/nav/2026-07-09.txt"], False),
        (["CTD/SP2613_Station1.hex CTD/SP2613_Station1.hdr"], True),
        (["CTD/Station 1 a.XMLCON"], True),  # names can contain spaces
        (["", "CTD/cast.bl"], True),
    ],
)
def test_has_ctd_changes(changed, expected):
    assert openvdm.has_ctd_changes(changed) is expected


def test_tracklines_extent(tmp_path):
    trackline(tmp_path / "a.geojson", [(-117.4, 32.6), (-117.2, 32.7)])
    trackline(tmp_path / "sub" / "b.geojson", [(-117.5, 32.55)])
    (tmp_path / "broken.geojson").write_text("{not json")
    extent = openvdm.tracklines_extent(tmp_path)
    assert (extent.westernmost, extent.easternmost) == (-117.5, -117.2)
    assert (extent.southernmost, extent.northernmost) == (32.55, 32.7)
    assert openvdm.tracklines_extent(tmp_path / "missing") is None


def test_resolve_paths_and_metadata(tmp_path):
    trackline(tmp_path / "SP2613/OpenVDM/Tracklines/gps.geojson", [(-117.4, 32.6), (-117.2, 32.7)])
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(vessel_name="Sproul"), "CTD")
    assert run.fileset_dir == tmp_path / "SP2613/CTD"
    assert run.output_dir == tmp_path / "SP2613/Products/CTD_QA"
    assert run.extent_source == "tracklines"
    config = run.config
    assert config.identifier == "SP2613_ctd"  # no R2R fileset ID
    assert (config.cruise_pi, config.depart_port.name) == ("A. Scientist", "San Diego, CA")
    assert config.manifest_path == tmp_path / "SP2613/MD5_Summary.txt"
    assert config.vessel_name == "Sproul" and run.owner == "survey"


def test_resolve_extent_fallbacks(tmp_path):
    site = openvdm.SiteConfig(extent=Extent(-118, -117, 32, 33))
    assert openvdm.resolve(cruise_config(tmp_path), site, "CTD").extent_source == "site config"
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "CTD")
    assert (run.extent_source, run.config.extent) == ("none", None)


def test_resolve_explains_missing_configuration(tmp_path):
    with pytest.raises(openvdm.OpenVDMError, match="no collection system transfer named 'XBT'"):
        openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "XBT")
    site = openvdm.SiteConfig(output_extra_directory="Reports")
    with pytest.raises(openvdm.OpenVDMError, match="Extra Directories"):
        openvdm.resolve(cruise_config(tmp_path), site, "CTD")


# OpenVDM's MD5 summary as the checksum manifest ------------------------------------------------


def test_md5_summary_manifest_and_pending_files(tmp_path):
    ctd = tmp_path / "CTD"
    ctd.mkdir()
    for name in ("good.hex", "bad.hex", "late.hex", "big.hex"):
        (ctd / name).write_text(name)
    summary = tmp_path / "MD5_Summary.txt"
    summary.write_text(
        f"{md5sum(ctd / 'good.hex')} CTD/good.hex\n"
        f"{'0' * 32} CTD/bad.hex\n"
        f"{md5sum(ctd / 'late.hex')} CTD/late.hex\n"
        f"{'*' * 32} CTD/big.hex\n"  # over OpenVDM's hashing size limit
        f"{'1' * 32} SCS/other.txt\n"  # outside the CTD directory
    )
    os.utime(summary, (1_000_000, 1_000_000))
    for name in ("good.hex", "bad.hex"):
        os.utime(ctd / name, (900_000, 900_000))
    (ctd / "late.hex").write_text("rewritten after the summary")  # newer mtime

    fileset = load_fileset(ctd, manifest_path=summary)
    assert [e.path for e in fileset.manifest] == ["bad.hex", "good.hex", "late.hex"]
    result = checksum_test(fileset)
    assert result.failures == ["bad.hex"]
    assert result.pending == ["late.hex"]
    assert (result.passed, result.total) == (1, 2)


# The hook end to end ---------------------------------------------------------------------------


@pytest.fixture
def openvdm_api(tmp_path):
    """A stand-in OpenVDM web API serving getCruiseConfig for a cruise under tmp_path"""
    response = cruise_config(tmp_path / "warehouse")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("/api/warehouse/getCruiseConfig"):
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", tmp_path / "warehouse" / "SP2613"
    server.shutdown()


def test_hook_writes_reports_into_the_extra_directory(openvdm_api):
    site_root, cruise_dir = openvdm_api
    source = r2r_fileset("SP2613_169847_ctd") / "data"
    ctd = cruise_dir / "CTD"
    ctd.mkdir(parents=True)
    for file in source.iterdir():
        shutil.copy2(file, ctd / file.name)
    summary = cruise_dir / "MD5_Summary.txt"
    summary.write_text("".join(f"{md5sum(f)} CTD/{f.name}\n" for f in sorted(ctd.iterdir())))
    trackline(
        cruise_dir / "OpenVDM/Tracklines/SP2613_gps.geojson",
        [(-117.3773, 32.5997), (-117.2262, 32.7054)],
    )

    # A transfer without CTD files is skipped
    assert main(["openvdm", "CTD", "--site-root", site_root, "--changed-files="]) == 0
    assert not (cruise_dir / "Products").exists()

    args = ["openvdm", "CTD", "--site-root", site_root, "--changed-files=CTD/SP2613_Station4.bl"]
    assert main(args) == 0
    output = cruise_dir / "Products/CTD_QA"
    for name in (
        "SP2613_ctd_qa.2.0.xml",
        "SP2613_ctd_qa_report.pdf",
        "SP2613_ctd_qa.ipynb",
        "SP2613_ctd_cruise.toml",
    ):
        assert (output / name).exists(), name
    assert len(list((output / "SP2613_ctd_plots").glob("*.svg"))) == 13

    root = ET.parse(output / "SP2613_ctd_qa.2.0.xml").getroot()
    assert root.findtext("r2r:identifier", namespaces=NS) == "SP2613_ctd_qa"
    ratings = {
        t.get("name"): t.findtext("r2r:rating", namespaces=NS)
        for t in root.iter(f"{{{R2R_NAMESPACE}}}test")
    }
    # Checksums from OpenVDM's MD5 summary; the cruise box from the trackline
    assert set(ratings.values()) == {"G"}, ratings
    assert root.findtext("r2r:certificate/r2r:rating", namespaces=NS) == "G"

    # The notebook re-runs from the snapshot of what OpenVDM supplied
    notebook = (output / "SP2613_ctd_qa.ipynb").read_text()
    assert "SP2613_ctd_cruise.toml" in notebook


def test_hook_reports_failures_to_openvdm(openvdm_api, capsys):
    site_root, _ = openvdm_api
    assert main(["openvdm", "XBT", "--site-root", site_root]) == 1
    assert "no collection system transfer named 'XBT'" in capsys.readouterr().err
