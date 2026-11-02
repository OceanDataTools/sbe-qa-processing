"""The PDF report's figures: parsed only when their page is drawn, at svglib's size."""

import matplotlib.pyplot as plt
from conftest import r2r_fileset
from svglib.svglib import svg2rlg

from sbe_qa_processing import pdf_report
from sbe_qa_processing.qa import run_qa


def test_a_figure_is_parsed_only_when_drawn_at_svglibs_size(tmp_path, monkeypatch):
    figure, axis = plt.subplots(figsize=(7.5, 4.3))
    axis.plot([0, 1], [0, 1])
    path = pdf_report._save_svg(figure, tmp_path / "figure.svg")
    parsed = []
    monkeypatch.setattr(pdf_report, "svg2rlg", lambda name: parsed.append(name) or svg2rlg(name))
    flowable = pdf_report._drawing(path, 300, max_height=150)
    assert parsed == []  # nothing parsed to lay out the page
    drawing = svg2rlg(str(path))
    factor = min(300 / drawing.width, 150 / drawing.height)
    assert flowable.wrap(500, 500) == (drawing.width * factor, drawing.height * factor)


def test_the_report_parses_each_figure_once_as_its_page_is_drawn(tmp_path, monkeypatch):
    result = run_qa(r2r_fileset("SP2613_169847_ctd"))
    parsed = []
    monkeypatch.setattr(pdf_report, "svg2rlg", lambda name: parsed.append(name) or svg2rlg(name))
    pdf_report.write_pdf(result, tmp_path / "report.pdf", tmp_path / "plots")
    svgs = sorted(str(p) for p in (tmp_path / "plots").glob("*.svg"))
    assert sorted(parsed) == svgs  # every figure, each once
