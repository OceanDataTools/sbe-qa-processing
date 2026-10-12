from datetime import date

from conftest import CONFIGS, FIXTURES

from sbe_qa_processing.cli import main
from sbe_qa_processing.config import QAConfig, Thresholds, load_config
from sbe_qa_processing.qa import resolve_config

QA_XML = str(FIXTURES / "SP2613_169847_r2r_qa.2.0.xml")


def test_thresholds_override_and_reject_unknown(tmp_path):
    path = tmp_path / "SP2613.toml"
    main(["r2r", "config-from-qa", QA_XML, "-o", str(tmp_path)])
    path.write_text(path.read_text() + "\n[thresholds]\nsalinity_range = [0.0, 42.0]\n")
    assert load_config(path).thresholds.salinity_range == (0.0, 42.0)
    path.write_text(path.read_text() + "typo_threshold = 1\n")
    try:
        load_config(path)
    except ValueError as error:
        assert "typo_threshold" in str(error)
    else:
        raise AssertionError("unknown threshold accepted")


def test_partial_configs_load(tmp_path):
    path = tmp_path / "partial.toml"
    path.write_text("[thresholds]\nsalinity_range = [0.0, 42.0]\n")
    config = load_config(path)
    assert type(config) is QAConfig
    assert config.thresholds.salinity_range == (0.0, 42.0)
    assert (config.cruise_id, config.extent, config.depart_date) == ("", None, None)

    path.write_text(
        '[cruise]\nid = "LAKE1"\ndepart_date = 2026-07-09\n\n[cruise.extent]\n'
        "westernmost = -88\neasternmost = -87\nsouthernmost = 42\nnorthernmost = 43\n"
    )
    config = load_config(path)
    assert config.extent.contains(42.5, -87.5)
    assert (config.depart_date, config.arrive_date) == (date(2026, 7, 9), None)


def test_core_config_ignores_r2r_fields():
    config = load_config(CONFIGS / "SP2613.toml")
    assert type(config) is QAConfig
    assert (config.identifier, config.depart_date) == ("SP2613", date(2026, 7, 9))


def test_core_config_ignores_r2r_thresholds(tmp_path):
    path = tmp_path / "core.toml"
    path.write_text("[thresholds]\nyellow_minimum_percent = 75.0\n")
    assert load_config(path).thresholds == Thresholds()


def test_resolve_config_titles_after_the_fileset(tmp_path):
    fileset = tmp_path / "MY_CASTS"
    fileset.mkdir()
    assert resolve_config(None, fileset).identifier == "MY_CASTS"
    assert resolve_config(QAConfig(cruise_id="LAKE1"), fileset).identifier == "LAKE1"


def test_run_without_a_config(tmp_path, capsys):
    fileset = tmp_path / "MY_CASTS"
    fileset.mkdir()
    output = tmp_path / "output"
    assert main(["run", str(fileset), "-o", str(output)]) == 0
    assert "MY_CASTS: 0 casts" in capsys.readouterr().out
    assert {p.name for p in output.iterdir()} == {
        "MY_CASTS_qa_report.pdf",
        "MY_CASTS_plots",
        "MY_CASTS_qa.ipynb",
    }
    notebook = (output / "MY_CASTS_qa.ipynb").read_text()
    assert "CONFIG = None" in notebook and "write_qa_xml" not in notebook


def test_run_with_an_r2r_cruise_toml_writes_only_core_reports(tmp_path):
    fileset = tmp_path / "MY_CASTS"
    fileset.mkdir()
    output = tmp_path / "output"
    assert main(["run", str(fileset), "-c", str(CONFIGS / "SP2613.toml"), "-o", str(output)]) == 0
    assert {p.name for p in output.iterdir()} == {
        "SP2613_qa_report.pdf",
        "SP2613_plots",
        "SP2613_qa.ipynb",
    }
