"""site-config: the per-ship TOML for the OpenVDM hook, from prompts."""

import io
import tomllib

import pytest

from sbe_qa_processing.cli import main
from sbe_qa_processing.config import Extent, Thresholds
from sbe_qa_processing.integrations.openvdm.hook import (
    SiteConfig,
    load_site_config,
    site_config_to_toml,
)


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
        "Roger Revelle",
        "Scripps Institution of Oceanography",
        "not an email",  # re-asked
        "tech@example.org",
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
    assert (site.vessel_name, site.contact_email) == ("Roger Revelle", "tech@example.org")
    assert site.output_extra_directory == "CTD_QA"
    assert site.extent == Extent(-125.0, -115.0, 30.0, 36.0)
    prompts = capsys.readouterr().err
    assert "not an email address" in prompts and "outside ±90" in prompts
    assert "southernmost must be <= northernmost" in prompts
    assert "R2R" not in prompts


def test_prompted_extent_across_the_antimeridian(tmp_path, monkeypatch, capsys):
    answer(monkeypatch, *[""] * 4, "178", "-178", "-20", "-15")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    assert load_site_config(output).extent == Extent(178.0, -178.0, -20.0, -15.0)
    assert "a box across the antimeridian" in capsys.readouterr().err


OLD_SITE_TOML = """\
[vessel]
id = "33RR"
name = "Roger Revelle"

[cruise]
operator_id = "edu.ucsd.sio"
scheduler_id = "org.unols"

[openvdm]
tracklines_extra_directory = "Tracklines"

[provenance]
distro_type = "post-cruise"
contact_institution_id = "edu.ucsd.sio"
contact_email = "tech@example.org"

[thresholds]
yellow_minimum_percent = 75.0
"""


def test_old_r2r_settings_are_ignored(tmp_path):
    path = tmp_path / "site.toml"
    path.write_text(OLD_SITE_TOML)
    site = load_site_config(path)
    assert (site.vessel_name, site.contact_email) == ("Roger Revelle", "tech@example.org")
    assert site.thresholds == Thresholds()
    assert site.ignored == [
        "[vessel] id",
        "[cruise] operator_id",
        "[cruise] scheduler_id",
        "[provenance] distro_type",
        "[provenance] contact_institution_id",
        "[openvdm] tracklines_extra_directory",
        "[thresholds] yellow_minimum_percent",
    ]


def test_rewriting_an_old_site_toml_drops_r2r_settings(tmp_path, monkeypatch, capsys):
    output = tmp_path / "site.toml"
    output.write_text(OLD_SITE_TOML)
    answer(monkeypatch)  # end of input: every field keeps its default
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    assert "Dropping [vessel] id, [cruise] operator_id" in capsys.readouterr().err
    data = tomllib.loads(output.read_text())
    assert data["vessel"] == {"name": "Roger Revelle"}
    assert "cruise" not in data and data["provenance"] == {"contact_email": "tech@example.org"}
    assert load_site_config(output).ignored == []


def test_overwrites_only_with_force_starting_from_the_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "site.toml"
    existing = SiteConfig(vessel_name="Revelle", extent=Extent(-125.0, -115.0, 30.0, 36.0))
    output.write_text(site_config_to_toml(existing))
    answer(monkeypatch, "Sproul")
    assert main(["site-config", "-o", str(output)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert load_site_config(output) == existing

    answer(monkeypatch, "-", "Scripps")  # clear the vessel name; a contact institution
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    site = load_site_config(output)
    assert (site.vessel_name, site.contact_institution) == ("", "Scripps")
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
"""


def test_settings_in_openvdm_yaml_are_not_asked_for(tmp_path, monkeypatch, capsys):
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text(OPENVDM_YAML)
    answer(
        monkeypatch,
        "tech@example.org",  # contact email: empty in openvdm.yaml, so asked
        "",  # reports extra directory
        "",  # no expected area
    )
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    site = load_site_config(output)
    assert (site.vessel_name, site.contact_institution) == ("", "")
    assert site.contact_email == "tech@example.org"
    prompts = capsys.readouterr().err
    assert "vessel_name = Roger Revelle" in prompts and "Vessel name" not in prompts
    assert "Contact email" in prompts
    assert "No openvdm.yaml" not in prompts


def test_openvdm_yaml_without_a_vessel_block(tmp_path, monkeypatch, capsys):
    # OpenVDM before 2.17: every field is asked for
    openvdm_yaml = tmp_path / "openvdm.yaml"
    openvdm_yaml.write_text('siteRoot: "http://127.0.0.1/"\n')
    answer(monkeypatch, "Roger Revelle")
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(openvdm_yaml), "-o", str(output)]) == 0
    assert load_site_config(output).vessel_name == "Roger Revelle"
    assert "aren't asked for" not in capsys.readouterr().err


def test_says_when_openvdm_yaml_is_not_at_the_default_path(tmp_path, monkeypatch, capsys):
    # e.g. OpenVDM installed outside /opt, or the site TOML written away from the server
    answer(monkeypatch, "Roger Revelle")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    prompts = capsys.readouterr().err
    assert f"No openvdm.yaml at {tmp_path / 'missing.yaml'}" in prompts
    assert "use --openvdm-config" in prompts
    assert "Vessel name" in prompts  # every field is asked for
    assert load_site_config(output).vessel_name == "Roger Revelle"


def test_missing_openvdm_config_is_an_error(tmp_path, capsys):
    missing = tmp_path / "elsewhere" / "openvdm.yaml"
    output = tmp_path / "site.toml"
    assert main(["site-config", "--openvdm-config", str(missing), "-o", str(output)]) == 1
    err = capsys.readouterr().err
    assert f"no openvdm.yaml at {missing}" in err and "--site-root" not in err
    assert not output.exists()


def test_site_theme_round_trips(tmp_path):
    site = SiteConfig(theme={"page_size": "A4", "colors": {"primary": "#123456"}})
    path = tmp_path / "site.toml"
    path.write_text(site_config_to_toml(site))
    assert load_site_config(path) == site
    # Without one, the file shows how to set it
    path.write_text(site_config_to_toml(SiteConfig()))
    assert "# [theme]" in path.read_text()


def test_unknown_site_keys_are_an_error(tmp_path):
    from sbe_qa_processing.config import ConfigError

    path = tmp_path / "site.toml"
    path.write_text('[vessel]\nnme = "Revelle"\n[cruise]\nname = "Revelle"\n')
    with pytest.raises(ConfigError) as raised:
        load_site_config(path)
    assert str(raised.value) == (
        f"{path}: unknown keys: [vessel] nme: did you mean name?; "
        "[cruise] name: did you mean [vessel] name?"
    )
