import tomllib
from datetime import date

from conftest import FIXTURES

from sbe_qa_processing.cli import main
from sbe_qa_processing.config import config_from_r2r_api, config_to_toml, load_config


def test_config_from_r2r_round_trips(tmp_path):
    path = tmp_path / "SP2613.toml"
    assert (
        main(["config-from-r2r", str(FIXTURES / "SP2613_169847_r2r_qa.2.0.xml"), "-o", str(path)])
        == 0
    )
    tomllib.loads(path.read_text())
    config = load_config(path)
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
    path = tmp_path / "SP2613.toml"
    path.write_text(config_to_toml(config_from_r2r_api(R2R_CRUISE, R2R_FILESET)))
    config = load_config(path)
    main(["config-from-r2r", str(FIXTURES / "SP2613_169847_r2r_qa.2.0.xml"), "-o", str(path)])
    expected = load_config(path)
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


def test_thresholds_override_and_reject_unknown(tmp_path):
    base = FIXTURES / "SP2613_169847_r2r_qa.2.0.xml"
    path = tmp_path / "c.toml"
    main(["config-from-r2r", str(base), "-o", str(path)])
    path.write_text(path.read_text() + "\n[thresholds]\nsalinity_range = [0.0, 42.0]\n")
    assert load_config(path).thresholds.salinity_range == (0.0, 42.0)
    path.write_text(path.read_text() + "typo_threshold = 1\n")
    try:
        load_config(path)
    except ValueError as error:
        assert "typo_threshold" in str(error)
    else:
        raise AssertionError("unknown threshold accepted")
