"""site-config: the per-ship TOML from prompts, optionally pre-filled from R2R's cruise API."""

import io
import json
import tomllib

import pytest

from sbe_qa_processing import config as config_module
from sbe_qa_processing.cli import main
from sbe_qa_processing.config import Extent, Thresholds
from sbe_qa_processing.openvdm import SiteConfig, load_site_config, site_config_to_toml

# A trimmed record from R2R's api/cruise for RR2605
R2R_CRUISE = {
    "cruise_id": "RR2605",
    "vessel_ices_code": "33RR",
    "vessel_id": "Roger Revelle",
    "vessel_shortname": "Revelle",
    "operator_id": "edu.ucsd.sio",
    "scheduler_id": "org.unols",
    "depart_date": "2026-03-01",
    "arrive_date": "2026-03-20",
}


@pytest.fixture
def r2r_api(monkeypatch):
    """R2R's API answering with recorded records; returns the URLs requested"""
    requested = []

    def urlopen(url, timeout):
        requested.append(url)
        data = [R2R_CRUISE] if "cruise_id=RR2605" in url else None
        # R2R answers "not found" with a 204 status in the body
        body = {"status": 200, "data": data} if data else {"status": 204, "data": None}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(config_module.urllib.request, "urlopen", urlopen)
    return requested


@pytest.fixture(autouse=True)
def no_openvdm_install(monkeypatch, tmp_path):
    """site-config reads /opt/openvdm's openvdm.yaml when it exists; not in these tests"""
    monkeypatch.setattr("sbe_qa_processing.cli.DEFAULT_OPENVDM_CONFIG", tmp_path / "missing.yaml")


def answer(monkeypatch, *lines: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(f"{line}\n" for line in lines)))


def test_site_toml_round_trips(tmp_path):
    for site in (
        SiteConfig(),  # blank fields are written commented out
        SiteConfig(
            vessel_id="33RR",
            vessel_name='The "Revelle"',
            contact_email="tech@example.org",
            extent=Extent(-125.0, -115.0, 30.0, 36.0),
            output_extra_directory="Products",
            thresholds=Thresholds(salinity_range=(0.0, 42.0)),
        ),
    ):
        path = tmp_path / "site.toml"
        path.write_text(site_config_to_toml(site))
        assert load_site_config(path) == site


def test_prompts(tmp_path, monkeypatch, capsys):
    answer(
        monkeypatch,
        "33RR",  # vessel ID
        "Roger Revelle",
        "edu.ucsd.sio",  # operator
        "",  # scheduler: blank
        "Scripps Institution of Oceanography",
        "edu.ucsd.sio",
        "not an email",  # re-asked
        "tech@example.org",
        "",  # distribution type: default
        "",  # reports extra directory: default
        "-125",  # westernmost
        "-115",  # easternmost
        "36",  # southernmost
        "30",  # northernmost, south of southernmost: the box is re-asked
        "-125",
        "-115",
        "30",
        "91",  # outside ±90, re-asked
        "36",
    )
    output = tmp_path / "configs" / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    site = load_site_config(output)
    assert (site.vessel_id, site.vessel_name, site.scheduler_id) == ("33RR", "Roger Revelle", "")
    assert site.contact_email == "tech@example.org"
    assert (site.distro_type, site.output_extra_directory) == ("post-cruise", "CTD_QA")
    assert site.extent == Extent(-125.0, -115.0, 30.0, 36.0)
    prompts = capsys.readouterr().err
    assert "not an email address" in prompts and "outside ±90" in prompts
    assert "southernmost must be <= northernmost" in prompts


def test_prompted_extent_across_the_antimeridian(tmp_path, monkeypatch, capsys):
    answer(monkeypatch, *[""] * 9, "178", "-178", "-20", "-15")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    assert load_site_config(output).extent == Extent(178.0, -178.0, -20.0, -15.0)
    assert "a box across the antimeridian" in capsys.readouterr().err


def test_old_tracklines_setting_is_ignored(tmp_path, caplog):
    path = tmp_path / "site.toml"
    path.write_text('[openvdm]\ntracklines_extra_directory = "Tracklines"\n')
    assert load_site_config(path) == SiteConfig()
    assert "tracklines_extra_directory is no longer used" in caplog.text


def test_from_r2r_without_a_terminal(tmp_path, monkeypatch, capsys, r2r_api):
    answer(monkeypatch)  # end of input: every field keeps its default
    assert main(["site-config", "--from-r2r", "RR2605", "-o", "-"]) == 0
    assert r2r_api == ["https://service.rvdata.us/api/cruise/?cruise_id=RR2605"]
    out = capsys.readouterr().out
    data = tomllib.loads(out)
    assert data["vessel"] == {"id": "33RR", "name": "Revelle"}
    assert data["cruise"] == {"operator_id": "edu.ucsd.sio", "scheduler_id": "org.unols"}
    assert "extent" not in data

    assert main(["site-config", "--from-r2r", "XX0000", "-o", "-"]) == 1
    assert "R2R has no cruise XX0000" in capsys.readouterr().err


def test_overwrites_only_with_force_starting_from_the_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "site.toml"
    existing = SiteConfig(
        vessel_id="33RR", vessel_name="Revelle", extent=Extent(-125.0, -115.0, 30.0, 36.0)
    )
    output.write_text(site_config_to_toml(existing))
    answer(monkeypatch, "32QU")
    assert main(["site-config", "-o", str(output)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert load_site_config(output) == existing

    answer(monkeypatch, "32QU", "-")  # a new vessel ID; clear the vessel name
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    site = load_site_config(output)
    assert (site.vessel_id, site.vessel_name) == ("32QU", "")
    assert site.extent == existing.extent  # kept as the default


OPENVDM_YAML = """\
siteRoot: "http://127.0.0.1/"
vessel:
    name: "Roger Revelle"
    contact:
        institution: "Scripps Institution of Oceanography"
        email: ""
    r2r:
        vesselID: "33RR"
        operatorID: "edu.ucsd.sio"
        schedulerID: "org.unols"
"""


def test_settings_in_openvdm_yaml_are_not_asked_for(tmp_path, monkeypatch, capsys):
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text(OPENVDM_YAML)
    answer(
        monkeypatch,
        "",  # contact institution's R2R ID: blank, so the operator ID
        "tech@example.org",  # contact email: empty in openvdm.yaml, so asked
        "",  # distribution type
        "",  # reports extra directory
        "",  # no fallback extent
    )
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    site = load_site_config(output)
    assert (site.vessel_id, site.vessel_name, site.contact_institution) == ("", "", "")
    assert (site.contact_email, site.contact_institution_id) == ("tech@example.org", "")
    prompts = capsys.readouterr().err
    assert "vessel_id = 33RR" in prompts and "R2R vessel ID" not in prompts
    assert "Contact email" in prompts
    assert "No openvdm.yaml" not in prompts


def test_openvdm_yaml_without_a_vessel_block(tmp_path, monkeypatch, capsys):
    # OpenVDM before 2.17: every field is asked for
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text('siteRoot: "http://127.0.0.1/"\n')
    answer(monkeypatch, "33RR")
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    assert load_site_config(output).vessel_id == "33RR"
    assert "aren't asked for" not in capsys.readouterr().err


def test_says_when_openvdm_yaml_is_not_at_the_default_path(tmp_path, monkeypatch, capsys):
    # e.g. OpenVDM installed outside /opt, or the site TOML written away from the server
    answer(monkeypatch, "33RR")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    prompts = capsys.readouterr().err
    assert f"No openvdm.yaml at {tmp_path / 'missing.yaml'}" in prompts
    assert "use --openvdm-config" in prompts
    assert "R2R vessel ID" in prompts  # every field is asked for
    assert load_site_config(output).vessel_id == "33RR"


def test_missing_openvdm_config_is_an_error(tmp_path, capsys):
    missing = tmp_path / "elsewhere" / "openvdm.yaml"
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(missing), "-o", str(output)]) == 1
    err = capsys.readouterr().err
    assert f"no openvdm.yaml at {missing}" in err and "--site-root" not in err
    assert not output.exists()
