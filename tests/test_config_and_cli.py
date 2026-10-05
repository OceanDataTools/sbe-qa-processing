import tomllib
from datetime import date

from conftest import FIXTURES

from sbe_qa_processing.cli import main
from sbe_qa_processing.config import load_config


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
