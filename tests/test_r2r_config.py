import tomllib
from datetime import date

import pytest
from conftest import CONFIGS, FIXTURES

from sbe_qa_processing.cli import main
from sbe_qa_processing.integrations.r2r.catalog import config_from_r2r_api
from sbe_qa_processing.integrations.r2r.config import (
    CruiseConfig,
    config_to_toml,
    load_cruise_config,
)

QA_XML = str(FIXTURES / "SP2613_169847_r2r_qa.2.0.xml")


def test_config_from_r2r_qa_round_trips(tmp_path):
    path = tmp_path / "SP2613.toml"
    assert main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)]) == 0
    tomllib.loads(path.read_text())
    config = load_cruise_config(path)
    assert (config.cruise_id, config.fileset_id) == ("SP2613", "169847")
    assert config.depart_date == config.arrive_date == date(2026, 7, 9)
    assert config.extent.contains(32.604167, -117.367333)
    assert not config.extent.contains(32.0, -117.3)


# Trimmed records from R2R's api/cruise and api/fileset for SP2613
R2R_CRUISE = {
    "cruise_id": "SP2613",
    "cruise_name": "MPL Internship Program Day at Sea",
    "vessel_ices_code": "32QU",
    "vessel_id": "Robert Gordon Sproul",
    "vessel_shortname": "Sproul",
    "operator_id": "edu.ucsd.sio",
    "scheduler_id": "org.unols",
    "waterbody_name": None,
    "depart_date": "2026-07-09",
    "depart_port_id": "100055",
    "depart_port_name": "San Diego",
    "arrive_date": "2026-07-09",
    "arrive_port_id": "100055",
    "arrive_port_name": "San Diego",
    "chief_scientist": "Pereira, Filipe",
    "longitude_min": "-117.3773103608",
    "longitude_max": "-117.2262308092",
    "latitude_min": "32.5997440998",
    "latitude_max": "32.7053695673",
}
R2R_FILESET = {"cruise_id": "SP2613", "fileset_id": 169847, "device_type": "ctd"}


def test_config_from_r2r_api_matches_the_qa_report(tmp_path):
    path = tmp_path / "api.toml"
    path.write_text(config_to_toml(config_from_r2r_api(R2R_CRUISE, R2R_FILESET)))
    config = load_cruise_config(path)
    main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)])
    expected = load_cruise_config(tmp_path / "SP2613.toml")
    for name in ("cruise_id", "fileset_id", "depart_date", "arrive_date", "extent"):
        assert getattr(config, name) == getattr(expected, name), name
    # The QA report names the vessel by its ICES code, the API's vessel_ices_code
    assert config.vessel_id == expected.vessel_id == "32QU"
    assert (config.vessel_name, config.cruise_pi) == ("Sproul", "Pereira, Filipe")
    assert (config.depart_port.name, config.depart_port.port_id) == ("San Diego", "100055")


def test_config_from_r2r_api_without_navigation():
    cruise = R2R_CRUISE | {"latitude_min": None, "latitude_max": None}
    config = config_from_r2r_api(cruise)
    assert config.extent is None
    assert config.fileset_id == ""


@pytest.mark.parametrize("name", ["SP2613", "RR2605", "BH18-18"])
def test_validation_configs_load(name):
    config = load_cruise_config(CONFIGS / f"{name}.toml")
    assert isinstance(config, CruiseConfig)
    assert config.identifier == f"{name}_{config.fileset_id}"


def test_yellow_minimum_is_r2r_config(tmp_path):
    path = tmp_path / "SP2613.toml"
    main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)])
    path.write_text(path.read_text() + "\n[thresholds]\nyellow_minimum_percent = 75.0\n")
    config = load_cruise_config(path)
    assert config.yellow_minimum_percent == 75.0
    assert "yellow_minimum_percent = 75.0" in config_to_toml(config)


def test_cruise_config_needs_the_id_and_dates(tmp_path):
    path = tmp_path / "LAKE1.toml"
    path.write_text('[cruise]\nid = "LAKE1"\ndepart_date = 2026-07-09\n')
    with pytest.raises(ValueError, match="R2R needs \\[cruise\\] arrive_date"):
        load_cruise_config(path)


def test_r2r_notebook_defines_names_before_using_them(tmp_path):
    from sbe_qa_processing.integrations.r2r.reports import notebook_extras, run_r2r
    from sbe_qa_processing.notebook import build_notebook

    fileset = tmp_path / "SP2613_169847_ctd"
    fileset.mkdir()
    run = run_r2r(fileset, CONFIGS / "SP2613.toml")
    notebook = build_notebook(
        run.result, tmp_path, notebook_extras(run, "SP2613_169847_qa.2.0.xml")
    )
    code = "\n".join(c.source for c in notebook.cells if c.cell_type == "code")
    compile(code.replace("%matplotlib inline", ""), "notebook", "exec")
    imported = code.index("import load_cruise_config")
    assert code.index("load_cruise_config(CONFIG)") > imported
    assert "CONFIG = Path(" in code and "certificate(result, r2r)" in code


def test_core_knows_the_keys_r2r_reads(tmp_path):
    """config.INTEGRATION_KEYS, written out as core never imports an integration, against what
    R2R's cruise TOML writer writes: a config with every field set loads back unchanged
    """
    from sbe_qa_processing.config import PORT_KEYS, Extent, Thresholds, fields_of
    from sbe_qa_processing.integrations.r2r.config import Port

    assert list(PORT_KEYS) == fields_of(Port)
    port = Port("San Diego", "100055", "US", "CA", -117.2, 32.7)
    config = CruiseConfig(
        cruise_id="SP2613",
        cruise_name="Day at sea",
        cruise_pi="A. Scientist",
        cruise_location="San Diego",
        vessel_name="Sproul",
        contact_institution="Scripps",
        contact_email="tech@example.org",
        extent=Extent(-117.4, -117.2, 32.5, 32.8),
        depart_date=date(2026, 7, 9),
        arrive_date=date(2026, 7, 9),
        manifest_path=tmp_path / "md5.txt",
        thresholds=Thresholds(salinity_range=(0.0, 42.0)),
        fileset_id="169847",
        vessel_id="32QU",
        operator_id="edu.ucsd.sio",
        scheduler_id="org.unols",
        depart_port=port,
        arrive_port=port,
        distro_type="post-cruise",
        contact_institution_id="edu.ucsd.sio",
        yellow_minimum_percent=75.0,
    )
    path = tmp_path / "SP2613.toml"
    path.write_text(config_to_toml(config))
    assert load_cruise_config(path) == config
