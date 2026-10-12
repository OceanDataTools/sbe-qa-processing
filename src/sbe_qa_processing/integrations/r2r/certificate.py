"""R2R's certificate: the rating, tests and infos, as PDF pages and notebook cells."""

from nbformat.v4 import new_code_cell, new_markdown_cell
from reportlab.lib import colors
from reportlab.platypus import Flowable, PageBreak, Paragraph, Spacer, Table, TableStyle

from sbe_qa_processing.integrations.r2r.config import CruiseConfig
from sbe_qa_processing.integrations.r2r.rules import R2RResult
from sbe_qa_processing.pdf_report import FrontPages, Layout, table, tint
from sbe_qa_processing.qa import QAResult

RATING_NAMES = {"G": "GREEN", "Y": "YELLOW", "R": "RED", "N": "GREY", "X": "BLACK"}


def r2r_facts(config: CruiseConfig) -> list[str]:
    """R2R's catalog details for the certificate, leaving out what isn't known"""
    ports = " to ".join(p.name for p in (config.depart_port, config.arrive_port) if p.name)
    facts = [
        f"R2R fileset {config.fileset_id}" if config.fileset_id else "",
        f"vessel ID {config.vessel_id}" if config.vessel_id else "",
        ports,
    ]
    return [fact for fact in facts if fact]


def certificate(result: QAResult, r2r: R2RResult) -> FrontPages:
    """The certificate pages, for write_pdf's front; their colors come from the theme"""

    def pages(lay: Layout) -> list[Flowable]:
        rating_colors = lay.colors.rating
        story = [Paragraph(" · ".join(r2r_facts(result.config)), lay.small), Spacer(1, 6)]
        banner = Table(
            [[Paragraph(f"R2R rating: {RATING_NAMES[r2r.rating]} ({r2r.rating})", lay.banner)]],
            colWidths=[lay.frame_width],
        )
        banner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(rating_colors[r2r.rating])),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story += [banner, Spacer(1, 3), Paragraph(r2r.description, lay.small), Spacer(1, 10)]

        rows, styles = [], []
        for i, test in enumerate(r2r.tests, start=1):
            rows.append(
                [
                    test.name,
                    test.rating,
                    "" if test.result is None else f"{test.result:g}%",
                    f"{test.passed}/{test.total}" if test.total else "",
                    ", ".join(test.failures),
                ]
            )
            styles.append(("BACKGROUND", (1, i), (1, i), tint(rating_colors[test.rating])))
        infos = [[i.name, i.value or "—"] for i in r2r.infos]
        story += [
            Paragraph("Tests", lay.heading),
            table(
                lay,
                ["Test", "Rating", "Result", "Passed", "Failures"],
                rows,
                [0.36, 0.08, 0.09, 0.09, 0.38],
                styles,
            ),
            Spacer(1, 12),
            Paragraph("Infos", lay.heading),
            table(lay, ["Info", "Value"], infos, [0.42, 0.58]),
        ]
        return story + [PageBreak()]

    return pages


NOTEBOOK_CELLS = [
    new_markdown_cell(
        "## R2R certificate\n\n"
        "The tests and infos written to the R2R QA 2.0 XML. Deck tests (file names with "
        "deck/dock/test) are left out of the NAV tests, as R2R does."
    ),
    new_code_cell(
        "display(pd.DataFrame([\n"
        '    {"test": t.name, "rating": t.rating, "result": t.result, "passed": t.passed,\n'
        '     "total": t.total, "failures": ", ".join(t.failures)}\n'
        "    for t in r2r.tests\n"
        "]))\n"
        'display(pd.DataFrame([{"info": i.name, "value": i.value} for i in r2r.infos]))'
    ),
]
