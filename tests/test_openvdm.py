"""The OpenVDM hook, against a stand-in OpenVDM API and an OpenVDM-style cruise directory."""

import json
import os
import shutil
import subprocess
import threading
import tomllib
import xml.etree.ElementTree as ET
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml
from conftest import r2r_fileset

from sbe_qa_processing import openvdm
from sbe_qa_processing.cli import main
from sbe_qa_processing.config import R2R_NAMESPACE, Extent, load_config
from sbe_qa_processing.fileset import load_fileset, md5sum
from sbe_qa_processing.r2r import checksum_test

NS = {"r2r": R2R_NAMESPACE}


# OpenVDM's cruiseExtent around SP2613's casts
SP2613_EXTENT = {
    "westernmost": -117.3773,
    "easternmost": -117.2262,
    "southernmost": 32.5997,
    "northernmost": 32.7054,
}


def cruise_config(base_dir: Path, cruise_id="SP2613", extent: dict | None = None) -> dict:
    """A getCruiseConfig response, trimmed to what the hook reads"""
    return {
        "cruiseID": cruise_id,
        "cruiseExtent": extent,
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
            "cruiseConfigFn": "ovdmConfig.json",
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


@pytest.mark.parametrize(
    "value, expected",
    [
        (SP2613_EXTENT, Extent(-117.3773, -117.2262, 32.5997, 32.7054)),
        # across the antimeridian, as OpenVDM and R2R give it
        (
            {**SP2613_EXTENT, "westernmost": 178, "easternmost": -178},
            Extent(178, -178, 32.5997, 32.7054),
        ),
        (None, None),  # no track yet, or OpenVDM before 2.17
        ({"westernmost": -117.4}, None),  # malformed
        ({**SP2613_EXTENT, "northernmost": "north"}, None),
    ],
)
def test_openvdm_extent(value, expected):
    assert openvdm.openvdm_extent(value) == expected


def test_resolve_paths_and_metadata(tmp_path):
    response = cruise_config(tmp_path, extent=SP2613_EXTENT)
    run = openvdm.resolve(response, openvdm.SiteConfig(vessel_name="Sproul"), "CTD")
    assert run.fileset_dir == tmp_path / "SP2613/CTD"
    assert run.output_dir == tmp_path / "SP2613/Products/CTD_QA"
    assert run.extent_source == "openvdm"
    assert run.config.extent == Extent(-117.3773, -117.2262, 32.5997, 32.7054)
    config = run.config
    assert config.identifier == "SP2613_ctd"  # no R2R fileset ID
    assert (config.cruise_pi, config.depart_port.name) == ("A. Scientist", "San Diego, CA")
    assert config.manifest_path == tmp_path / "SP2613/MD5_Summary.txt"
    assert config.vessel_name == "Sproul" and run.owner == "survey"


def test_resolve_extent_fallbacks(tmp_path):
    site = openvdm.SiteConfig(extent=Extent(-118, -117, 32, 33))
    # OpenVDM's extent is the cruise's own, so it wins over the site config's
    run = openvdm.resolve(cruise_config(tmp_path, extent=SP2613_EXTENT), site, "CTD")
    assert (run.extent_source, run.config.extent.westernmost) == ("openvdm", -117.3773)
    run = openvdm.resolve(cruise_config(tmp_path), site, "CTD")
    assert (run.extent_source, run.config.extent) == ("site config", site.extent)
    assert "OpenVDM has no cruise extent" in run.notes[0]
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "CTD")
    assert (run.extent_source, run.config.extent) == ("none", None)


def test_resolve_explains_missing_configuration(tmp_path):
    with pytest.raises(openvdm.OpenVDMError, match="no collection system transfer named 'XBT'"):
        openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "XBT")
    site = openvdm.SiteConfig(output_extra_directory="Reports")
    with pytest.raises(openvdm.OpenVDMError, match="Extra Directories"):
        openvdm.resolve(cruise_config(tmp_path), site, "CTD")


# OpenVDM 2.17's vessel block, as in openvdm.yaml and ovdmConfig.json
VESSEL = {
    "name": "Robert Gordon Sproul",
    "contact": {"institution": "Scripps Institution of Oceanography", "email": "ops@ucsd.edu"},
    "r2r": {"vesselID": "32QU", "operatorID": "edu.ucsd.sio", "schedulerID": "org.unols"},
}


@pytest.mark.parametrize(
    "block, expected",
    [
        (
            VESSEL,
            {
                "vessel_name": "Robert Gordon Sproul",
                "vessel_id": "32QU",
                "operator_id": "edu.ucsd.sio",
                "scheduler_id": "org.unols",
                "contact_institution": "Scripps Institution of Oceanography",
                "contact_email": "ops@ucsd.edu",
            },
        ),
        # A ship that doesn't submit to R2R has no r2r block; empty values are unset
        (
            {"name": "Falkor (too)", "contact": {"institution": "", "email": None}},
            {"vessel_name": "Falkor (too)"},
        ),
        ({"r2r": {"vesselID": 3301}}, {"vessel_id": "3301"}),  # an ID stays a string
        (None, {}),  # openvdm.yaml from before 2.17
        ({"name": {"unexpected": "table"}, "r2r": "unexpected"}, {}),
    ],
)
def test_vessel_settings(block, expected):
    assert openvdm.vessel_settings(block, "openvdm.yaml").settings == expected


def test_resolve_prefers_openvdms_vessel_settings(tmp_path):
    site = openvdm.SiteConfig(vessel_id="32ST", scheduler_id="org.unols", distro_type="test")
    vessel = openvdm.vessel_settings(VESSEL, "openvdm.yaml")
    run = openvdm.resolve(cruise_config(tmp_path), site, "CTD", vessel=vessel)
    config = run.config
    assert (config.vessel_id, config.vessel_name) == ("32QU", "Robert Gordon Sproul")
    assert (config.operator_id, config.contact_email) == ("edu.ucsd.sio", "ops@ucsd.edu")
    # OpenVDM doesn't store the contact's R2R ID: on board, that's the operator
    assert config.contact_institution_id == "edu.ucsd.sio"
    assert config.distro_type == "test"  # not in OpenVDM
    assert run.vessel_source == "openvdm.yaml"
    # A disagreement is noted; an agreement isn't
    expected = (
        "vessel_id is '32QU' in OpenVDM's openvdm.yaml and '32ST' in the site config; "
        "using openvdm.yaml's"
    )
    assert [n for n in run.notes if "vessel_id" in n] == [expected]
    assert not any("scheduler_id" in note for note in run.notes)
    assert not any("no vessel ID" in note for note in run.notes)

    # The site config's contact ID, and fields OpenVDM leaves unset, still apply
    site = openvdm.SiteConfig(contact_institution_id="edu.ucsd", contact_email="tech@example.org")
    vessel = openvdm.vessel_settings({"r2r": {"operatorID": "edu.ucsd.sio"}}, "openvdm.yaml")
    config = openvdm.resolve(cruise_config(tmp_path), site, "CTD", vessel=vessel).config
    assert (config.contact_institution_id, config.contact_email) == (
        "edu.ucsd",
        "tech@example.org",
    )


def test_resolve_reads_the_vessel_from_ovdmconfig_json(tmp_path, caplog):
    cruise_dir = tmp_path / "SP2613"
    cruise_dir.mkdir()
    site = openvdm.SiteConfig(vessel_name="Sproul")
    # No ovdmConfig.json yet: the site config alone
    run = openvdm.resolve(cruise_config(tmp_path), site, "CTD")
    assert (run.vessel_source, run.config.vessel_name) == ("none", "Sproul")
    assert any("no vessel ID: set vessel.r2r.vesselID" in note for note in run.notes)

    (cruise_dir / "ovdmConfig.json").write_text(
        json.dumps({"cruiseID": "SP2613", "vessel": VESSEL})
    )
    run = openvdm.resolve(cruise_config(tmp_path), site, "CTD")
    assert (run.vessel_source, run.config.vessel_id) == ("ovdmConfig.json", "32QU")
    assert run.config.vessel_name == "Robert Gordon Sproul"

    (cruise_dir / "ovdmConfig.json").write_text("{not json")
    run = openvdm.resolve(cruise_config(tmp_path), site, "CTD")
    assert (run.vessel_source, run.config.vessel_name) == ("none", "Sproul")
    assert "can't read the vessel settings" in caplog.text


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


# Adding the reports to OpenVDM's MD5 summary ---------------------------------------------------


def openvdm_install(root: Path) -> Path:
    """A stand-in OpenVDM install with the MD5 summary script, as from OpenVDM 2.16.1"""
    for path in ("venv/bin/python", "utils/update_md5_summary.py", "server/etc/openvdm.yaml"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    return root


class FakeRun:
    """subprocess.run standing in for OpenVDM's script; records the commands"""

    def __init__(self, returncode=0, stderr="", error=None):
        self.commands, self.returncode, self.stderr, self.error = [], returncode, stderr, error

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


def written_reports(tmp_path):
    """A HookRun for SP2613 and report files in its output directory"""
    run = openvdm.resolve(cruise_config(tmp_path), openvdm.SiteConfig(), "CTD")
    plots = run.output_dir / "SP2613_ctd_plots"
    plots.mkdir(parents=True)
    for name in ("cast_map.svg", "station1_ts.svg"):
        (plots / name).write_text("<svg/>")
    pdf = run.output_dir / "SP2613_ctd_qa_report.pdf"
    pdf.write_text("%PDF")
    return run, [pdf, plots]


def test_openvdm_install_dir():
    assert openvdm.openvdm_install_dir("/srv/openvdm/server/etc/openvdm.yaml") == Path(
        "/srv/openvdm"
    )


def test_queue_md5_update(tmp_path, monkeypatch):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_reports(tmp_path / "warehouse")
    fake = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert openvdm.queue_md5_update(install, run, written) is None
    # Every file, the plots directory's too, relative to the cruise directory
    assert fake.commands == [
        [
            str(install / "venv/bin/python"),
            str(install / "utils/update_md5_summary.py"),
            "--cruiseID",
            "SP2613",
            "Products/CTD_QA/SP2613_ctd_plots/cast_map.svg",
            "Products/CTD_QA/SP2613_ctd_plots/station1_ts.svg",
            "Products/CTD_QA/SP2613_ctd_qa_report.pdf",
        ]
    ]


@pytest.mark.parametrize(
    "fake, expected",
    [
        (
            FakeRun(1, "Traceback ...\nUnable to queue the MD5 summary update: no Gearman\n"),
            "exited 1: Unable to queue the MD5 summary update: no Gearman",
        ),
        (FakeRun(error=OSError("Permission denied")), "couldn't run"),
        (FakeRun(error=subprocess.TimeoutExpired("python", 120)), "timed out"),
    ],
)
def test_queue_md5_update_failures(tmp_path, monkeypatch, fake, expected):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_reports(tmp_path / "warehouse")
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert expected in openvdm.queue_md5_update(install, run, written)


def test_queue_md5_update_without_the_script(tmp_path, monkeypatch):
    # OpenVDM before 2.16.1
    run, written = written_reports(tmp_path / "warehouse")
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun())
    problem = openvdm.queue_md5_update(tmp_path / "openvdm", run, written)
    assert "update_md5_summary.py" in problem and "OpenVDM 2.16.1" in problem


def test_queue_md5_update_outside_the_cruise(tmp_path, monkeypatch):
    install = openvdm_install(tmp_path / "openvdm")
    run, _ = written_reports(tmp_path / "warehouse")
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_text("%PDF")
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun())
    assert "outside the cruise directory" in openvdm.queue_md5_update(install, run, [elsewhere])


# The hook end to end ---------------------------------------------------------------------------


@pytest.fixture
def api_response(tmp_path):
    """The getCruiseConfig response openvdm_api serves; tests can change it"""
    return cruise_config(tmp_path / "warehouse")


@pytest.fixture
def openvdm_api(tmp_path, api_response):
    """A stand-in OpenVDM web API serving getCruiseConfig for a cruise under tmp_path"""
    response = api_response

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


def test_hook_writes_reports_into_the_extra_directory(
    openvdm_api, api_response, tmp_path, monkeypatch, caplog
):
    site_root, cruise_dir = openvdm_api
    # With --site-root, OpenVDM's MD5 script is looked for in the default install
    install = openvdm_install(tmp_path / "openvdm")
    monkeypatch.setattr(
        "sbe_qa_processing.cli.DEFAULT_OPENVDM_CONFIG", install / "server/etc/openvdm.yaml"
    )
    md5_update = FakeRun(1, "Unable to queue the MD5 summary update: no Gearman")
    monkeypatch.setattr(openvdm.subprocess, "run", md5_update)
    source = r2r_fileset("SP2613_169847_ctd") / "data"
    ctd = cruise_dir / "CTD"
    ctd.mkdir(parents=True)
    for file in source.iterdir():
        shutil.copy2(file, ctd / file.name)
    summary = cruise_dir / "MD5_Summary.txt"
    summary.write_text("".join(f"{md5sum(f)} CTD/{f.name}\n" for f in sorted(ctd.iterdir())))
    api_response["cruiseExtent"] = SP2613_EXTENT

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
    # Checksums from OpenVDM's MD5 summary; the cruise box from OpenVDM's cruiseExtent
    assert set(ratings.values()) == {"G"}, ratings
    assert root.findtext("r2r:certificate/r2r:rating", namespaces=NS) == "G"

    # The notebook re-runs from the snapshot of what OpenVDM supplied
    notebook = (output / "SP2613_ctd_qa.ipynb").read_text()
    assert "SP2613_ctd_cruise.toml" in notebook

    # Every report goes to OpenVDM's MD5 summary; a failure to queue that doesn't fail the hook
    (command,) = md5_update.commands
    assert command[2:4] == ["--cruiseID", "SP2613"]
    queued = set(command[4:])
    assert {
        "Products/CTD_QA/SP2613_ctd_cruise.toml",
        "Products/CTD_QA/SP2613_ctd_qa.ipynb",
    } < queued
    assert len([f for f in queued if f.endswith(".svg")]) == 13
    assert "the reports aren't in OpenVDM's MD5 summary" in caplog.text


def test_hook_reports_failures_to_openvdm(openvdm_api, capsys):
    site_root, _ = openvdm_api
    assert main(["openvdm", "XBT", "--site-root", site_root]) == 1
    assert "no collection system transfer named 'XBT'" in capsys.readouterr().err


# config-from-openvdm ---------------------------------------------------------------------------


def test_config_from_openvdm_reports_gaps(openvdm_api, tmp_path, capsys):
    site_root, _ = openvdm_api
    site = tmp_path / "site.toml"
    site.write_text('[vessel]\nid = "32QU"\nname = "Sproul"\n')
    configs = tmp_path / "configs"
    args = ["config-from-openvdm", "--site-root", site_root, "--site-config", str(site)]
    assert main([*args, "--fileset-id", "169847", "-o", str(configs)]) == 0

    config = load_config(configs / "SP2613.toml")
    assert (config.cruise_id, config.fileset_id, config.vessel_id) == ("SP2613", "169847", "32QU")
    assert (config.depart_date, config.arrive_date) == (date(2026, 7, 9), date(2026, 7, 10))
    assert config.extent is None
    assert config.manifest_path == tmp_path / "warehouse/SP2613/MD5_Summary.txt"
    err = capsys.readouterr().err
    assert "cruise extent from none" in err and "Lat/Lon test is GREY (N)" in err
    assert "no R2R port IDs" in err and "no end date" not in err
    # The notes are kept in the file, for whoever reviews it
    assert "# Note: no cruise extent" in (configs / "SP2613.toml").read_text()

    # The file isn't replaced without --force
    assert main([*args, "-o", str(configs)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert main([*args, "-o", str(configs), "--force"]) == 0
    assert load_config(configs / "SP2613.toml").fileset_id == ""


def test_config_from_openvdm_to_stdout(openvdm_api, api_response, tmp_path, capsys):
    site_root, _ = openvdm_api
    api_response |= {"cruiseEndDate": "", "cruiseStartPortID": 100055, "cruiseEndPortID": "100055"}
    api_response["cruiseExtent"] = SP2613_EXTENT
    assert main(["config-from-openvdm", "--site-root", site_root, "-o", "-"]) == 0
    captured = capsys.readouterr()
    data = tomllib.loads(captured.out)
    assert data["cruise"]["depart_port"] == {"name": "San Diego, CA", "port_id": "100055"}
    assert data["cruise"]["arrive_date"] == date.today()  # noqa: DTZ011 - a calendar date
    assert data["cruise"]["extent"]["westernmost"] == -117.3773
    assert "cruise extent from openvdm" in captured.err
    assert "no end date; arrive_date is today" in captured.err
    assert "no R2R port IDs" not in captured.err and "no vessel ID" in captured.err


def test_config_from_openvdm_explains_missing_configuration(openvdm_api, capsys):
    site_root, _ = openvdm_api
    assert main(["config-from-openvdm", "XBT", "--site-root", site_root, "-o", "-"]) == 1
    assert "no collection system transfer named 'XBT'" in capsys.readouterr().err


def test_config_from_openvdm_with_openvdm_yaml(openvdm_api, tmp_path, capsys):
    site_root, cruise_dir = openvdm_api
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text(yaml.safe_dump({"siteRoot": site_root, "vessel": VESSEL}))
    # openvdm.yaml is current, so it wins over an older ovdmConfig.json
    cruise_dir.mkdir(parents=True)
    old_vessel = {"name": "Sproul", "r2r": {"vesselID": "32ST"}}
    (cruise_dir / "ovdmConfig.json").write_text(json.dumps({"vessel": old_vessel}))
    args = ["config-from-openvdm", "--openvdm-config", str(openvdm_yaml), "-o", "-"]
    assert main(args) == 0
    captured = capsys.readouterr()
    cruise = tomllib.loads(captured.out)["cruise"]
    assert (cruise["vessel_id"], cruise["vessel_name"]) == ("32QU", "Robert Gordon Sproul")
    assert "vessel settings from openvdm.yaml" in captured.err

    # With --site-root there's no openvdm.yaml: the cruise's ovdmConfig.json
    assert main(["config-from-openvdm", "--site-root", site_root, "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert tomllib.loads(captured.out)["cruise"]["vessel_id"] == "32ST"
    assert "vessel settings from ovdmConfig.json" in captured.err


def test_openvdm_yaml_not_found(tmp_path, capsys):
    # OpenVDM installed outside /opt, without --openvdm-config in the hook
    missing = tmp_path / "openvdm.yaml"
    assert main(["openvdm", "--openvdm-config", str(missing)]) == 1
    err = capsys.readouterr().err
    assert f"no openvdm.yaml at {missing}" in err and "--site-root" in err
    assert main(["config-from-openvdm", "--openvdm-config", str(missing), "-o", "-"]) == 1
    assert "give its path with --openvdm-config" in capsys.readouterr().err
