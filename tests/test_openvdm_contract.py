"""The OpenVDM hook against OpenVDM's own code, not stand-ins we wrote: how OpenVDM runs the hook
command INSTALL.md documents, its utils/update_md5_summary.py as the hook calls it, and the
cruise config file it writes for each cruise. Stand-ins for these let --cruiseID (which no
release of the script accepts) and the old ovdmConfig.json name through. Run with a checkout of
OpenVDM's 2.17 branch with a venv of its requirements (OPENVDM_SOURCE, default ../openvdm), as
CI does; skipped without one.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import ROOT
from test_openvdm import cruise_config

from sbe_qa_processing.cli import build_parser
from sbe_qa_processing.integrations.openvdm import hook as openvdm

SOURCE = Path(os.environ.get("OPENVDM_SOURCE", ROOT.parent / "openvdm")).absolute()
PYTHON = SOURCE / "venv" / "bin" / "python"
TESTS = Path(__file__).parent

FOUND = (SOURCE / "server/workers/post_hooks.py").exists() and PYTHON.exists()
MISSING = (
    f"no OpenVDM checkout with a venv at {SOURCE}: set OPENVDM_SOURCE to OpenVDM's 2.17 branch"
)
if os.environ.get("OPENVDM_CONTRACT_REQUIRED") and not FOUND:
    raise RuntimeError(MISSING)  # CI's contract job: a skip would pass unnoticed
pytestmark = pytest.mark.skipif(not FOUND, reason=MISSING)


def run_openvdm(action: str, **request) -> dict:
    """tests/openvdm_driver.py's answer, from OpenVDM's code under OpenVDM's Python"""
    completed = subprocess.run(
        [str(PYTHON), str(TESTS / "openvdm_driver.py")],
        input=json.dumps({"action": action, **request}),
        capture_output=True,
        text=True,
        env={**os.environ, "OPENVDM_SOURCE": str(SOURCE)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def api_cruise_config(base_dir: Path) -> dict:
    """getCruiseConfig as OpenVDM's API gives it, with what its export removes"""
    config = cruise_config(base_dir)
    config["warehouseConfig"].update(
        cruiseConfigFn="cruise_config.json",  # OpenVDM's default, CRUISE_CONFIG_FN
        loweringConfigFn="lowering_config.json",
        dataDashboardManifestFn="manifest.json",
        md5SummaryMd5Fn="MD5_Summary.md5",
    )
    config.update(cruiseDataTransfersConfig=[], shipToShoreTransfersConfig=[])
    return config


# The hook INSTALL.md documents ---------------------------------------------------------------


def install_md_hooks() -> dict:
    text = (ROOT / "INSTALL.md").read_text()
    (block,) = [
        b for b in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL) if "postHookCommands" in b
    ]
    return yaml.safe_load(block)["postHookCommands"]


def test_openvdm_runs_the_documented_hook(tmp_path):
    """OpenVDM's post_hooks worker fills INSTALL.md's hook command, and our CLI parses it"""
    settings = yaml.safe_load((SOURCE / "server/etc/openvdm.yaml.dist").read_text())
    assert "postCollectionSystemTransfer" in settings["postHookCommands"]
    settings["postHookCommands"]["postCollectionSystemTransfer"] = install_md_hooks()[
        "postCollectionSystemTransfer"
    ]
    config = tmp_path / "openvdm.yaml"
    config.write_text(yaml.safe_dump(settings))
    files = {"new": ["SP2613_Station1.hex", "SP2613_Station1.hdr"], "updated": []}
    commands = run_openvdm(
        "hook_commands",
        openvdm_yaml=str(config),
        hook="postCollectionSystemTransfer",
        transfer={"collectionSystemTransferID": "4", "name": "CTD"},
        cruise_id="SP2613",
        files=files,
    )
    (command,) = commands["commandList"]
    assert "missing" not in command
    args = build_parser().parse_args(command["command"][1:])
    assert args.command == "openvdm" and args.transfer == "CTD"
    # OpenVDM joins a file list into one argument, and keeps "--changed-files=" without files
    assert args.changed_files == [" ".join(files["new"]), ""]
    assert openvdm.has_ctd_changes(args.changed_files)


# OpenVDM's update_md5_summary.py, as the hook runs it ---------------------------------------


def openvdm_install(tmp_path: Path) -> Path:
    """An install for queue_md5_update to find: OpenVDM's venv and its utils/"""
    install = tmp_path / "openvdm"
    install.mkdir()
    (install / "venv").symlink_to(SOURCE / "venv")
    (install / "utils").symlink_to(SOURCE / "utils")
    return install


def queue(tmp_path, monkeypatch, run, warehouse: Path) -> tuple[str | None, dict | None]:
    """queue_md5_update through OpenVDM's script; what went wrong, and the job it queued"""
    run.output_dir.mkdir(parents=True)
    report = run.output_dir / "SP2613_qa_report.pdf"
    report.write_text("")
    record = tmp_path / "job.json"
    monkeypatch.setenv("PYTHONPATH", str(TESTS / "openvdm_md5"))
    monkeypatch.setenv("OPENVDM_SOURCE", str(SOURCE))
    monkeypatch.setenv("OPENVDM_CONTRACT_RECORD", str(record))
    monkeypatch.setenv("OPENVDM_CONTRACT_WAREHOUSE", str(warehouse))
    problem = openvdm.queue_md5_update(openvdm_install(tmp_path), run, [report])
    return problem, json.loads(record.read_text()) if record.exists() else None


def test_openvdms_script_takes_the_hooks_md5_update(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    run = openvdm.resolve(api_cruise_config(warehouse), openvdm.SiteConfig(), "CTD")
    problem, job = queue(tmp_path, monkeypatch, run, warehouse)
    assert problem is None
    assert job["data"]["cruiseID"] == "SP2613"
    assert job["data"]["files"]["updated"] == ["Products/CTD_QA/SP2613_qa_report.pdf"]


# The cruise config file OpenVDM writes -------------------------------------------------------


def test_the_hook_reads_the_vessel_from_the_cruise_config_openvdm_writes(tmp_path):
    """Without openvdm.yaml, the vessel comes from the file OpenVDM's export writes"""
    warehouse = tmp_path / "warehouse"
    cruise_dir = warehouse / "SP2613"
    cruise_dir.mkdir(parents=True)
    api = api_cruise_config(warehouse)
    vessel = {"name": "Robert Gordon Sproul", "contact": {"email": "ops@example.org"}}
    exported = run_openvdm(
        "export_cruise_config", cruise_config=api, vessel=vessel, cruise_dir=str(cruise_dir)
    )
    assert exported == {"verdict": True}
    assert (cruise_dir / openvdm.CRUISE_CONFIG_FN).exists()
    del api["warehouseConfig"]["cruiseConfigFn"]  # OpenVDM's default name, without the API's
    run = openvdm.resolve(api, openvdm.SiteConfig(), "CTD")
    assert run.vessel_source == "cruise_config.json"
    assert (run.config.vessel_name, run.config.contact_email) == (
        "Robert Gordon Sproul",
        "ops@example.org",
    )


def test_a_cruise_from_the_file_openvdm_writes_resolves_as_from_its_api(tmp_path):
    """--cruise-config: the cruise from OpenVDM's export, as from OpenVDM's API"""
    warehouse = tmp_path / "warehouse"
    cruise_dir = warehouse / "SP2613"
    cruise_dir.mkdir(parents=True)
    api = api_cruise_config(warehouse)
    vessel = {"name": "Robert Gordon Sproul", "contact": {"email": "ops@example.org"}}
    run_openvdm(
        "export_cruise_config", cruise_config=api, vessel=vessel, cruise_dir=str(cruise_dir)
    )
    site = openvdm.SiteConfig()
    from_file = openvdm.resolve(
        openvdm.load_cruise_json(cruise_dir / "cruise_config.json"), site, "CTD"
    )
    from_api = openvdm.resolve(
        api, site, "CTD", vessel=openvdm.vessel_settings(vessel, "openvdm.yaml")
    )
    for name in (
        "cruise_id",
        "cruise_name",
        "cruise_pi",
        "cruise_location",
        "depart_date",
        "arrive_date",
        "extent",
        "manifest_path",
        "vessel_name",
        "contact_email",
    ):
        assert getattr(from_file.config, name) == getattr(from_api.config, name), name
    assert (from_file.fileset_dir, from_file.output_dir) == (
        from_api.fileset_dir,
        from_api.output_dir,
    )
