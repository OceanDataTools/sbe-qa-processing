"""End to end against R2R's own QA reports. Needs the filesets in data/ (see the skip message)."""

import xml.etree.ElementTree as ET

import nbformat
import pytest
from conftest import CONFIGS, r2r_fileset

from sbe_qa_processing.cli import main
from sbe_qa_processing.config import R2R_NAMESPACE

NS = {"r2r": R2R_NAMESPACE}


def canonical(element) -> str:
    return ET.canonicalize(ET.tostring(element, encoding="unicode"), strip_text=True)


@pytest.mark.parametrize(
    "name, cruise", [("SP2613_169847_ctd", "SP2613"), ("RR2605_170644_ctd", "RR2605")]
)
def test_matches_r2r_report(tmp_path, name, cruise):
    fileset = r2r_fileset(name)
    assert main(["run", str(CONFIGS / f"{cruise}.toml"), str(fileset), "-o", str(tmp_path)]) == 0
    identifier = name.removesuffix("_ctd")
    ours = ET.parse(tmp_path / f"{identifier}_qa.2.0.xml").getroot()
    reference = ET.parse(fileset / "r2r_qa.2.0.xml").getroot()

    # Ratings, test results, bounds and infos are identical to R2R's
    assert canonical(ours.find("r2r:certificate", NS)) == canonical(
        reference.find("r2r:certificate", NS)
    )
    files = lambda root: {
        (f.get("id"), f.get("checksum"), f.text) for f in root.iter(f"{{{R2R_NAMESPACE}}}file")
    }
    assert files(ours) == files(reference)
    assert ours.attrib == reference.attrib

    # Every figure is written as SVG and embedded in the PDF as vector graphics
    pdf = (tmp_path / f"{identifier}_qa_report.pdf").read_bytes()
    assert b"/Subtype /Image" not in pdf
    svgs = sorted((tmp_path / f"{identifier}_plots").glob("*.svg"))
    casts = sum(1 for c in fileset.joinpath("data").glob("*.hex") if "test" not in c.name.lower())
    assert len(svgs) == 1 + 3 * casts  # the map, then profiles/ts/pressure per cast
    assert all(svg.read_bytes().lstrip().startswith(b"<?xml") for svg in svgs)
    notebook = nbformat.read(tmp_path / f"{identifier}_qa.ipynb", as_version=4)
    nbformat.validate(notebook)


def test_failure_paths_on_bh18_18(tmp_path):
    fileset = r2r_fileset("BH18-18_132368_ctd")
    from sbe_qa_processing.qa import run_qa

    result = run_qa(CONFIGS / "BH18-18.toml", fileset)
    tests = {t.name: t for t in result.r2r.tests}
    assert tests["Presence of All Raw Files"].rating == "R"
    assert tests["Presence of All Raw Files"].failures == ["05252018_ScienceOnDeck"]
    infos = {i.name: i.value for i in result.r2r.infos}
    # An SBE 21 thermosalinograph configuration filed with the 911plus casts
    assert infos["Casts with XMLCON/con file in Bad Format"] == "05082018_DockTest"
    assert all(c.status != "fail" for checks in result.science.values() for c in checks)
