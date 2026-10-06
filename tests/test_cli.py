"""The command line conventions every command follows (issue 6)."""

import argparse
import io
import json
import tarfile
import tomllib

import pytest
from conftest import FIXTURES

from sbe_qa_processing import config as config_module
from sbe_qa_processing.cli import build_parser, main
from sbe_qa_processing.r2r_download import extract_bag

QA_XML = str(FIXTURES / "SP2613_169847_r2r_qa.2.0.xml")

# Trimmed records from R2R's api/cruise and api/fileset for SP2613
R2R_RECORDS = {
    "cruise": [
        {
            "cruise_id": "SP2613",
            "vessel_ices_code": "32QU",
            "depart_date": "2026-07-09",
            "arrive_date": "2026-07-09",
        }
    ],
    "fileset": [
        {"fileset_id": 169847, "device_type": "ctd", "label": "CTD"},
        {"fileset_id": 169850, "device_type": "gnss", "label": "GNSS"},
    ],
}


@pytest.fixture
def r2r_api(monkeypatch):
    """R2R's API answering for SP2613 with recorded records; returns the URLs requested"""
    requested = []

    def urlopen(url, timeout):
        requested.append(url)
        endpoint = url.split("/api/")[1].split("/")[0]
        data = R2R_RECORDS[endpoint] if "cruise_id=SP2613" in url else None
        # R2R answers "not found" with a 204 status in the body
        return io.BytesIO(json.dumps({"data": data}).encode())

    monkeypatch.setattr(config_module.urllib.request, "urlopen", urlopen)
    return requested


def subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def test_every_argument_is_documented():
    commands = subcommands()
    assert set(commands) == {
        "run",
        "openvdm",
        "config-from-r2r",
        "config-from-r2r-qa",
        "config-from-openvdm",
        "site-config",
        "fetch-fileset",
        "fetch-map-data",
    }
    for name, parser in commands.items():
        assert parser.description, name
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            label = f"{name} {action.dest}"
            assert action.help, label
            assert "--out" not in action.option_strings, label
            if action.default not in (None, False):
                assert "%(default)s" in action.help, label
            if action.nargs != 0:  # takes a value: a descriptive metavar
                assert action.metavar and action.metavar.isupper(), label
            if "-o" in action.option_strings:
                assert action.option_strings == ["-o", "--output"], label


def test_fileset_ids_are_integers(capsys):
    with pytest.raises(SystemExit):
        main(["config-from-r2r", "SP2613", "abc"])
    assert "not an R2R fileset ID" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["config-from-openvdm", "--fileset-id", "16984x"])


def test_run_refuses_to_replace_reports(tmp_path, capsys):
    main(["config-from-r2r-qa", QA_XML, "-o", str(tmp_path)])
    output = tmp_path / "output"
    output.mkdir()
    (output / "SP2613_169847_qa_report.pdf").write_text("an earlier report")
    args = ["run", str(tmp_path / "SP2613.toml"), str(tmp_path / "empty"), "-o", str(output)]
    assert main(args) == 1
    assert "use --force" in capsys.readouterr().err
    assert (output / "SP2613_169847_qa_report.pdf").read_text() == "an earlier report"


def test_config_from_r2r_qa_output(tmp_path, capsys):
    assert main(["config-from-r2r-qa", QA_XML, "-o", "-"]) == 0
    assert tomllib.loads(capsys.readouterr().out)["cruise"]["id"] == "SP2613"
    assert main(["config-from-r2r-qa", QA_XML, "-o", str(tmp_path)]) == 0
    assert main(["config-from-r2r-qa", QA_XML, "-o", str(tmp_path)]) == 1
    assert "use --force" in capsys.readouterr().err
    assert main(["config-from-r2r-qa", QA_XML, "-o", str(tmp_path), "--force"]) == 0


def test_config_from_r2r(tmp_path, capsys, r2r_api):
    assert main(["config-from-r2r", "SP2613", "169847", "-o", "-"]) == 0
    captured = capsys.readouterr()
    config = tomllib.loads(captured.out)
    assert (config["cruise"]["vessel_id"], config["fileset"]["id"]) == ("32QU", "169847")
    assert "no navigation bounding box" in captured.err

    assert main(["config-from-r2r", "SP2613", "169847", "-o", str(tmp_path)]) == 0
    requested = len(r2r_api)
    assert main(["config-from-r2r", "SP2613", "169847", "-o", str(tmp_path)]) == 1
    assert len(r2r_api) == requested  # refused before asking R2R
    assert "use --force" in capsys.readouterr().err


@pytest.mark.parametrize(
    "cruise, fileset, problem",
    [
        ("XX0000", "1", "R2R has no cruise XX0000"),
        ("SP2613", "169850", "SP2613 fileset 169850 is GNSS; its CTD filesets: 169847 (CTD)"),
        ("SP2613", "1", "SP2613 has no fileset 1"),
    ],
)
def test_config_from_r2r_explains_catalog_problems(cruise, fileset, problem, capsys, r2r_api):
    assert main(["config-from-r2r", cruise, fileset, "-o", "-"]) == 1
    assert problem in capsys.readouterr().err


def test_fetch_fileset(tmp_path, capsys, r2r_api):
    assert main(["fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 1
    assert "isn't released for download" in capsys.readouterr().err  # no download_url
    (tmp_path / "SP2613_169847_ctd").mkdir()
    requested = len(r2r_api)
    assert main(["fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 1
    assert len(r2r_api) == requested
    assert "use --force" in capsys.readouterr().err


def test_extract_bag_from_a_nested_archive(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, text in (
            ("SP2613/169847/bagit.txt", "BagIt-Version: 0.97\n"),
            ("SP2613/169847/data/cast.hex", "*END*\n"),
            ("SP2613/README", "outside the bag\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(text)
            tar.addfile(info, io.BytesIO(text.encode()))
    assert extract_bag(archive.getvalue(), tmp_path / "bag") == 2
    assert (tmp_path / "bag/bagit.txt").exists() and (tmp_path / "bag/data/cast.hex").exists()


def test_fetch_map_data(monkeypatch, capsys):
    monkeypatch.setattr("sbe_qa_processing.maps.prefetch", lambda: ["land 10m"])
    assert main(["fetch-map-data"]) == 0
    assert "cached land 10m" in capsys.readouterr().out
