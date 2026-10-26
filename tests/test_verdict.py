"""The checks in a line, under the PDF's title."""

from types import SimpleNamespace

from conftest import CONFIGS, r2r_fileset

from sbe_qa_processing.pdf_report import _title, layout
from sbe_qa_processing.qa import run_qa, verdict
from sbe_qa_processing.science import FAIL, INFO, NA, PASS, WARN, CheckResult


def result(fileset, science):
    """A QAResult's integrity and science checks, by (name, status[, value])"""
    check = lambda name, status, value="": CheckResult(name, status, value)
    return SimpleNamespace(
        integrity=SimpleNamespace(fileset=[check(*c) for c in fileset]),
        science={cast: [check(*c) for c in checks] for cast, checks in science.items()},
    )


def test_everything_passes():
    run = result(
        [("Checksums", PASS), ("Casts", INFO, "2")],
        {"CTD1": [("Spikes", PASS)], "CTD2": [("Spikes", PASS), ("Duration", INFO)]},
    )
    assert verdict(run) == (
        PASS,
        "The integrity check passes. All 2 casts pass the science checks.",
    )
    run.integrity.fileset.append(CheckResult("Cast times", NA, "none"))
    assert verdict(run)[1].startswith("1 of 2 integrity checks passes.")


def test_problems_are_named_with_their_values_and_casts():
    run = result(
        [
            ("Checksums", PASS),
            ("Position inside the expected area", WARN, "3 of 4 casts pass"),
            ("Cast time inside the expected dates", NA, "no expected dates in the config"),
        ],
        {
            "CTD1": [("Salinity agreement", WARN), ("Spikes", PASS)],
            "CTD2": [("Salinity agreement", FAIL), ("Spikes", WARN)],
            "CTD3": [("Salinity agreement", PASS), ("Spikes", PASS)],
        },
    )
    assert verdict(run) == (
        FAIL,
        (
            "1 of 3 integrity checks warns: Position inside the expected area (3 of 4 casts "
            "pass). 2 of 3 casts fail in the science checks: Salinity agreement (CTD1, CTD2), "
            "Spikes (CTD2). n/a: Cast time inside the expected dates."
        ),
    )


def test_a_check_in_every_cast_or_many_is_counted():
    every = {f"CTD{i}": [("Sensor calibration age", WARN)] for i in range(5)}
    assert verdict(result([], every))[1].endswith(
        "5 of 5 casts warn in the science checks: Sensor calibration age (all 5)."
    )
    many = {**every, "CTD9": [("Sensor calibration age", PASS)]}
    assert verdict(result([], many))[1].endswith(
        "5 of 6 casts warn in the science checks: Sensor calibration age (5 casts)."
    )


def test_nothing_to_judge_is_na():
    assert verdict(result([("Checksums", NA, "no manifest")], {})) == (
        NA,
        "No integrity check could be made. No cast for the science checks. n/a: Checksums.",
    )


def test_the_pdf_shows_it_under_the_title():
    run = run_qa(r2r_fileset("BH18-18_132368_ctd"), CONFIGS / "BH18-18.toml")
    lay = layout()
    texts = [
        cell.text
        for flowable in _title(lay, run)
        if hasattr(flowable, "_cellvalues")
        for row in flowable._cellvalues
        for cell in row
        if hasattr(cell, "text")
    ]
    assert run.verdict[1] in texts
    assert run.verdict[0] == FAIL  # BH18-18: a cast with only a .hdr
