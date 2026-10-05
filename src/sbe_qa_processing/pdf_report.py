"""Writes the PDF QA report: an R2R certificate summary, a fileset overview, then two pages per
converted cast.

Every plot is first written as an SVG file, and the PDF embeds those same SVGs as vector
drawings (ReportLab with svglib), so the figures stay sharp at any zoom and the SVGs can be
reused on their own.
"""

import re
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from svglib.svglib import svg2rlg

from sbe_qa_processing import plots
from sbe_qa_processing.bottles import bottle_fires
from sbe_qa_processing.loadout import cast_loadout, instrument_settings
from sbe_qa_processing.qa import QAResult
from sbe_qa_processing.science import FAIL, PASS, WARN

RATING_NAMES = {"G": "GREEN", "Y": "YELLOW", "R": "RED", "N": "GREY", "X": "BLACK"}
MARGIN = 0.6 * inch
FRAME_WIDTH = letter[0] - 2 * MARGIN
FRAME_HEIGHT = letter[1] - 2 * MARGIN - 12  # the frame's 6 pt top and bottom padding
CHART_HEIGHT = 4.6 * inch  # pressure record beside the bottle table, at most
MIN_CHART_HEIGHT = 2.8 * inch  # below this the plot moves to the next page

# DejaVu Sans ships with matplotlib and, unlike ReportLab's built-in base fonts, has glyphs
# such as ≤ ≥ ° σ Δ used in the report text
_FONTS = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVuSans", str(_FONTS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(_FONTS / "DejaVuSans-Bold.ttf")))
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "title",
    parent=_styles["Title"],
    alignment=TA_LEFT,
    fontSize=18,
    spaceAfter=4,
    fontName=FONT_BOLD,
)
HEADING = ParagraphStyle(
    "heading", parent=_styles["Heading2"], spaceBefore=0, spaceAfter=6, fontName=FONT_BOLD
)
SUBHEADING = ParagraphStyle(
    "subheading", parent=_styles["Heading3"], spaceBefore=2, spaceAfter=4, fontName=FONT_BOLD
)
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=11, fontName=FONT)
SMALL = ParagraphStyle(
    "small", parent=BODY, fontSize=7, leading=8.5, textColor=colors.HexColor("#555555")
)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7, leading=8.4)
CELL_BOLD = ParagraphStyle("cellbold", parent=CELL, fontName=FONT_BOLD)
BANNER = ParagraphStyle(
    "banner", parent=BODY, fontSize=15, leading=18, textColor=colors.white, fontName=FONT_BOLD
)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def _save_svg(figure: Figure, path: Path) -> Path:
    # No date in the metadata, so re-running gives identical files
    figure.savefig(path, format="svg", metadata={"Date": None})
    plt.close(figure)
    return path


def write_plot_svgs(result: QAResult, plots_dir: Path | str) -> dict[str, Path]:
    """Writes every report figure as SVG. Keys: "map", and "<cast>/profiles|ts|pressure"."""
    plots_dir = Path(plots_dir)
    plots_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "map": _save_svg(plots.cast_map(result.casts, result.config), plots_dir / "cast_map.svg")
    }
    for data in result.science_casts:
        slug = _slug(data.name)
        paths[f"{data.name}/profiles"] = _save_svg(
            plots.profiles(data), plots_dir / f"{slug}_profiles.svg"
        )
        paths[f"{data.name}/ts"] = _save_svg(plots.ts_diagram(data), plots_dir / f"{slug}_ts.svg")
        paths[f"{data.name}/pressure"] = _save_svg(
            plots.pressure_series(data), plots_dir / f"{slug}_pressure.svg"
        )
    return paths


def _drawing(path: Path, width: float, max_height: float | None = None) -> Flowable:
    """An SVG as a vector drawing scaled to `width` (and at most `max_height`)"""
    drawing = svg2rlg(str(path))
    scale = width / drawing.width
    if max_height is not None:
        scale = min(scale, max_height / drawing.height)
    drawing.scale(scale, scale)
    drawing.width *= scale
    drawing.height *= scale
    drawing.hAlign = "CENTER"
    return drawing


def _cell(text, style=CELL) -> Paragraph:
    return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;"), style)


def _table(header: list[str], rows: list[list], widths: list[float], extra_styles=()) -> Table:
    data = [[_cell(h, CELL_BOLD) for h in header]]
    data += [[c if isinstance(c, Flowable) else _cell(c) for c in row] for row in rows]
    table = Table(data, colWidths=[w * FRAME_WIDTH for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3e8ef")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa4b2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                *extra_styles,
            ]
        )
    )
    return table


def _tint(hex_color: str, alpha: float = 0.35) -> colors.Color:
    color = colors.HexColor(hex_color)
    return colors.Color(
        1 - alpha * (1 - color.red), 1 - alpha * (1 - color.green), 1 - alpha * (1 - color.blue)
    )


def _cruise_facts(config) -> list[str]:
    """Cruise metadata for the report header, leaving out what isn't known"""
    ports = " to ".join(p.name for p in (config.depart_port, config.arrive_port) if p.name)
    facts = [
        f"PI {config.cruise_pi}" if config.cruise_pi else "",
        f"vessel {config.vessel_name or config.vessel_id}"
        if (config.vessel_name or config.vessel_id)
        else "",
        f"{config.depart_date} to {config.arrive_date}",
        ports,
        config.cruise_location,
        f"R2R fileset {config.fileset_id}" if config.fileset_id else "",
    ]
    return [fact for fact in facts if fact]


def _certificate(result: QAResult) -> list[Flowable]:
    config, r2r = result.config, result.r2r
    story = [
        Paragraph(f"SBE 9/911plus QA report: {config.cruise_id}", TITLE),
        Paragraph(config.cruise_name or "", BODY),
        Spacer(1, 4),
        Paragraph(" · ".join(_cruise_facts(config)), SMALL),
        Paragraph(
            f"Generated {result.finished:%Y-%m-%d %H:%M} UTC by "
            + ", ".join(f"{k} {v}" for k, v in result.versions.items()),
            SMALL,
        ),
        Paragraph(f"Source: {result.fileset.root}", SMALL),
        Spacer(1, 10),
    ]
    banner = Table(
        [[Paragraph(f"R2R rating: {RATING_NAMES[r2r.rating]} ({r2r.rating})", BANNER)]],
        colWidths=[FRAME_WIDTH],
    )
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(plots.RATING_COLORS[r2r.rating])),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story += [banner, Spacer(1, 3), Paragraph(r2r.description, SMALL), Spacer(1, 10)]

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
        styles.append(("BACKGROUND", (1, i), (1, i), _tint(plots.RATING_COLORS[test.rating])))
    story += [
        Paragraph("Tests", HEADING),
        _table(
            ["Test", "Rating", "Result", "Passed", "Failures"],
            rows,
            [0.36, 0.08, 0.09, 0.09, 0.38],
            styles,
        ),
        Spacer(1, 12),
        Paragraph("Infos", HEADING),
        _table(["Info", "Value"], [[i.name, i.value or "—"] for i in r2r.infos], [0.42, 0.58]),
    ]
    return story


def _overview(result: QAResult, svgs: dict[str, Path]) -> list[Flowable]:
    rows, styles = [], []
    for i, data in enumerate(result.casts, start=1):
        checks = result.science.get(data.name, [])
        counts = {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL)}
        status = FAIL if counts[FAIL] else WARN if counts[WARN] else PASS if checks else "n/a"
        note = "deck test" if data.cast.is_deck_test else "; ".join(data.errors.values())
        rows.append(
            [
                data.name,
                str(data.scans),
                f"{round(float(data.pressure.max())) + 0:d}" if data.converted else "",
                "yes" if data.has_secondary else "no",
                f"{counts[PASS]}/{counts[WARN]}/{counts[FAIL]}" if checks else "",
                note,
            ]
        )
        styles.append(("BACKGROUND", (4, i), (4, i), colors.HexColor(plots.STATUS_COLORS[status])))
    return [
        Paragraph("Fileset overview", HEADING),
        _drawing(svgs["map"], FRAME_WIDTH, max_height=4.6 * inch),
        Spacer(1, 10),
        _table(
            ["Cast", "Scans", "Max P [dbar]", "Dual T/C", "Pass / warn / fail", "Notes"],
            rows,
            [0.22, 0.09, 0.11, 0.08, 0.16, 0.34],
            styles,
        ),
    ]


def _sensors(result: QAResult, data) -> Flowable:
    """Sensor loadout table, flagging calibrations older than the configured limit"""
    limit = result.config.thresholds.max_calibration_age_days
    rows, styles = [], []
    for i, row in enumerate(cast_loadout(data), start=1):
        age = "" if row.age_days is None else f"{row.age_days}"
        rows.append(
            [
                row.channel,
                row.sensor,
                row.serial_number,
                row.calibration_date.isoformat()
                if row.calibration_date
                else row.calibration_text or "—",
                age,
            ]
        )
        if row.age_days is not None and row.age_days > limit:
            styles.append(
                ("BACKGROUND", (4, i), (4, i), colors.HexColor(plots.STATUS_COLORS[WARN]))
            )
    return KeepTogether(
        [
            Paragraph("Sensors", SUBHEADING),
            Paragraph(instrument_settings(data), SMALL),
            Spacer(1, 4),
            _table(
                ["Channel", "Sensor", "Serial number", "Calibrated", "Age at cast [days]"],
                rows,
                [0.24, 0.33, 0.15, 0.14, 0.14],
                styles,
            ),
            Paragraph(f"Ages over {limit} days are highlighted.", SMALL),
        ]
    )


def _one_decimal(value: float) -> str:
    """One decimal place, without a "-0.0" for small negatives"""
    return f"{round(value, 1) + 0.0:.1f}"


def _bottle_table(data) -> Table | None:
    """Bottle fires with time and pressure, or None when no bottles were fired. The table may
    split across pages (header repeated) for very large rosettes
    """
    fires = bottle_fires(data)
    if not fires:
        return None

    def pressure(fire):
        return "—" if fire.pressure is None else _one_decimal(fire.pressure)

    def spread(fire):
        if fire.pressure is None:
            return ""
        return f"{_one_decimal(fire.pressure_min)} – {_one_decimal(fire.pressure_max)}"

    rows = [
        [
            str(fire.position),
            str(fire.sequence),
            fire.time.strftime("%Y-%m-%d %H:%M:%S") if fire.time else "—",
            pressure(fire),
            spread(fire),
        ]
        for fire in fires
    ]
    return _table(
        ["Bottle", "Fire order", "Time (UTC)", "Pressure, mean [dbar]", "Range [dbar]"],
        rows,
        [0.12, 0.14, 0.3, 0.2, 0.24],
    )


def _height(flowable) -> float:
    _, height = flowable.wrap(FRAME_WIDTH, FRAME_HEIGHT)
    return height + flowable.getSpaceBefore() + flowable.getSpaceAfter()


def _bottle_page(data, svgs: dict[str, Path]) -> list[Flowable]:
    """The bottle fire table with the pressure record below it, sized to the space left. A table
    too long to leave room for a readable plot (very large rosettes) pushes it to the next page
    """
    name = data.name
    table = _bottle_table(data)
    title = Paragraph(f"{name}: bottle fires and pressure record", HEADING)
    subtitle = Paragraph(f"Bottle fires ({table._nrows - 1})", SUBHEADING)
    gap = 8
    remaining = FRAME_HEIGHT - _height(title) - _height(subtitle) - _height(table) - gap - 4
    pressure = svgs[f"{name}/pressure"]
    if remaining >= MIN_CHART_HEIGHT:
        chart = [Spacer(1, gap), _drawing(pressure, FRAME_WIDTH, min(remaining, CHART_HEIGHT))]
    else:
        chart = [PageBreak(), _drawing(pressure, FRAME_WIDTH, CHART_HEIGHT)]
    return [title, subtitle, table, *chart]


def _cast(result: QAResult, data, svgs: dict[str, Path]) -> list[Flowable]:
    """Per cast: sensors and checks; then the pressure record (with the bottle fire table when
    bottles were fired); then the TS diagram, which always follows the pressure record; then
    profiles. Without bottle fires, the pressure record and TS diagram share a page
    """
    name = data.name
    checks = result.science[name]
    rows = [[c.name, c.status, c.value, c.limit, c.detail] for c in checks]
    styles = [
        ("BACKGROUND", (1, i), (1, i), colors.HexColor(plots.STATUS_COLORS[c.status]))
        for i, c in enumerate(checks, start=1)
    ]
    tables = [
        PageBreak(),
        Paragraph(name, HEADING),
        _sensors(result, data),
        Spacer(1, 6),
        KeepTogether(
            [
                Paragraph("Checks", SUBHEADING),
                _table(
                    ["Check", "Status", "Value", "Limit", "Detail"],
                    rows,
                    [0.27, 0.08, 0.18, 0.12, 0.35],
                    styles,
                ),
            ]
        ),
    ]
    profiles = [
        PageBreak(),
        Paragraph(f"{name}: profiles", HEADING),
        _drawing(svgs[f"{name}/profiles"], FRAME_WIDTH, max_height=8.9 * inch),
    ]
    if bottle_fires(data):
        return [
            *tables,
            PageBreak(),
            *_bottle_page(data, svgs),
            PageBreak(),
            Paragraph(f"{name}: TS diagram", HEADING),
            _drawing(svgs[f"{name}/ts"], FRAME_WIDTH, max_height=8.5 * inch),
            *profiles,
        ]
    return [
        *tables,
        PageBreak(),
        Paragraph(f"{name}: pressure record and TS diagram", HEADING),
        _drawing(svgs[f"{name}/pressure"], FRAME_WIDTH, max_height=4.1 * inch),
        Spacer(1, 6),
        _drawing(svgs[f"{name}/ts"], FRAME_WIDTH, max_height=4.9 * inch),
        *profiles,
    ]


def write_pdf(result: QAResult, path: Path | str, plots_dir: Path | str | None = None) -> Path:
    """Writes the PDF, and the SVG plots it embeds into `plots_dir` (default:
    <pdf directory>/<identifier>_plots)
    """
    path = Path(path)
    plots_dir = Path(plots_dir) if plots_dir else path.parent / f"{result.config.identifier}_plots"
    svgs = write_plot_svgs(result, plots_dir)

    title = f"SBE 9/911plus QA report: {result.config.identifier}"

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(MARGIN, 0.4 * inch, title)
        canvas.drawRightString(letter[0] - MARGIN, 0.4 * inch, f"page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=title,
        subject=f"R2R rating {result.r2r.rating}",
        creator=f"sbe-qa-processing {result.versions['sbe-qa-processing']}",
    )
    story = _certificate(result) + [PageBreak()] + _overview(result, svgs)
    for data in result.science_casts:
        story += _cast(result, data, svgs)
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path
