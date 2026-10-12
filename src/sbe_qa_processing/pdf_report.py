"""Writes the PDF QA report: a title block, any pages an integration adds (e.g. R2R's
certificate), a fileset overview, then two pages per converted cast.

Every plot is first written as an SVG file, and the PDF embeds those same SVGs as vector
drawings (ReportLab with svglib), so the figures stay sharp at any zoom and the SVGs can be
reused on their own.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
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
from sbe_qa_processing.theme import DEFAULT_THEME, Theme

# [in] tallest figures; each is also kept within the frame, which A4 or larger margins shrink
MAP_HEIGHT = 4.6
PROFILES_HEIGHT = 8.9
TS_HEIGHT = 8.5
SHARED_PRESSURE_HEIGHT, SHARED_TS_HEIGHT = 4.1, 4.9  # together on one page
CHART_HEIGHT = 4.6 * inch  # pressure record beside the bottle table, at most
MIN_CHART_HEIGHT = 2.8 * inch  # below this the plot moves to the next page
HEADING_ROOM = 0.5 * inch  # a page heading above a figure

# DejaVu Sans ships with matplotlib and, unlike ReportLab's built-in base fonts, has glyphs
# such as ≤ ≥ ° σ Δ used in the report text
_DEJAVU = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
DEFAULT_FONTS = (_DEJAVU / "DejaVuSans.ttf", _DEJAVU / "DejaVuSans-Bold.ttf")
PAGE_SIZES = {"letter": letter, "A4": A4}


def _register_font(path: Path) -> str:
    """ReportLab's name for a TTF, registering it once"""
    name = path.stem
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


@dataclass
class Layout:
    """A theme made concrete for ReportLab: page and frame sizes [pt], fonts and paragraph
    styles. Integrations build their pages (write_pdf's front) from the same Layout
    """

    theme: Theme
    page: tuple[float, float]
    margin: float
    frame_width: float
    frame_height: float
    font: str
    font_bold: str
    title: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    body: ParagraphStyle
    small: ParagraphStyle
    cell: ParagraphStyle
    cell_bold: ParagraphStyle
    banner: ParagraphStyle

    @property
    def colors(self):
        return self.theme.colors

    def fit(self, height: float) -> float:
        """height [pt], at most what's left of the frame below a page heading"""
        return min(height, self.frame_height - HEADING_ROOM)


def layout(theme: Theme = DEFAULT_THEME) -> Layout:
    page = PAGE_SIZES[theme.page_size]
    margin = theme.margin * inch
    font = _register_font(theme.font or DEFAULT_FONTS[0])
    font_bold = _register_font(theme.bold_font or theme.font or DEFAULT_FONTS[1])
    scale = theme.font_scale
    size = lambda points: points if scale == 1 else points * scale
    styles = getSampleStyleSheet()
    heading2, heading3 = styles["Heading2"], styles["Heading3"]
    body = ParagraphStyle(
        "body",
        parent=styles["BodyText"],
        fontSize=size(9),
        leading=size(11),
        fontName=font,
    )
    cell = ParagraphStyle("cell", parent=body, fontSize=size(7), leading=size(8.4))
    return Layout(
        theme=theme,
        page=page,
        margin=margin,
        frame_width=page[0] - 2 * margin,
        frame_height=page[1] - 2 * margin - 12,  # the frame's 6 pt top and bottom padding
        font=font,
        font_bold=font_bold,
        title=ParagraphStyle(
            "title",
            parent=styles["Title"],
            alignment=TA_LEFT,
            fontSize=size(18),
            leading=size(styles["Title"].leading),
            spaceAfter=4,
            fontName=font_bold,
        ),
        heading=ParagraphStyle(
            "heading",
            parent=heading2,
            fontSize=size(heading2.fontSize),
            leading=size(heading2.leading),
            spaceBefore=0,
            spaceAfter=6,
            fontName=font_bold,
        ),
        subheading=ParagraphStyle(
            "subheading",
            parent=heading3,
            fontSize=size(heading3.fontSize),
            leading=size(heading3.leading),
            spaceBefore=2,
            spaceAfter=4,
            fontName=font_bold,
        ),
        body=body,
        small=ParagraphStyle(
            "small",
            parent=body,
            fontSize=size(7),
            leading=size(8.5),
            textColor=colors.HexColor(theme.colors.muted_text),
        ),
        cell=cell,
        cell_bold=ParagraphStyle("cellbold", parent=cell, fontName=font_bold),
        banner=ParagraphStyle(
            "banner",
            parent=body,
            fontSize=size(15),
            leading=size(18),
            textColor=colors.white,
            fontName=font_bold,
        ),
    )


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def _save_svg(figure: Figure, path: Path) -> Path:
    # No date in the metadata, and a fixed salt for matplotlib's clip path IDs (random by
    # default), so re-running gives identical files
    with matplotlib.rc_context({"svg.hashsalt": "sbe-qa-processing"}):
        figure.savefig(path, format="svg", metadata={"Date": None})
    plt.close(figure)
    return path


def write_plot_svgs(
    result: QAResult, plots_dir: Path | str, theme: Theme = DEFAULT_THEME
) -> dict[str, Path]:
    """Writes every report figure as SVG. Keys: "map", and "<cast>/profiles|ts|pressure"."""
    plots_dir = Path(plots_dir)
    plots_dir.mkdir(parents=True, exist_ok=True)
    cast_map = plots.cast_map(result.casts, result.config, theme)
    paths = {"map": _save_svg(cast_map, plots_dir / "cast_map.svg")}
    for data in result.science_casts:
        slug = _slug(data.name)
        for key, make in (
            ("profiles", plots.profiles),
            ("ts", plots.ts_diagram),
            ("pressure", plots.pressure_series),
        ):
            paths[f"{data.name}/{key}"] = _save_svg(
                make(data, theme), plots_dir / f"{slug}_{key}.svg"
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


def _cell(text, style: ParagraphStyle) -> Paragraph:
    return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;"), style)


def table(
    lay: Layout, header: list[str], rows: list[list], widths: list[float], extra_styles=()
) -> Table:
    data = [[_cell(h, lay.cell_bold) for h in header]]
    data += [[c if isinstance(c, Flowable) else _cell(c, lay.cell) for c in row] for row in rows]
    table = Table(data, colWidths=[w * lay.frame_width for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(lay.colors.table_header)),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(lay.colors.table_grid)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                *extra_styles,
            ]
        )
    )
    return table


def tint(hex_color: str, alpha: float = 0.35) -> colors.Color:
    color = colors.HexColor(hex_color)
    return colors.Color(
        1 - alpha * (1 - color.red), 1 - alpha * (1 - color.green), 1 - alpha * (1 - color.blue)
    )


def _status_color(lay: Layout, status: str) -> colors.Color:
    return colors.HexColor(lay.colors.status[status])


def _cruise_facts(config) -> list[str]:
    """Cruise metadata for the report header, leaving out what isn't known"""
    dates = (
        f"{config.depart_date} to {config.arrive_date}"
        if config.depart_date and config.arrive_date
        else ""
    )
    facts = [
        f"PI {config.cruise_pi}" if config.cruise_pi else "",
        f"vessel {config.vessel_name}" if config.vessel_name else "",
        dates,
        config.cruise_location,
        f"contact {config.contact_institution}" if config.contact_institution else "",
    ]
    return [fact for fact in facts if fact]


LOGO_HEIGHT = 0.6 * inch
LOGO_MAX_WIDTH = 2.0 * inch


def _logo(path: Path) -> Image:
    """The theme's logo, at most LOGO_HEIGHT tall and LOGO_MAX_WIDTH wide"""
    logo = Image(str(path))
    scale = min(LOGO_HEIGHT / logo.imageHeight, LOGO_MAX_WIDTH / logo.imageWidth)
    logo.drawWidth, logo.drawHeight = logo.imageWidth * scale, logo.imageHeight * scale
    return logo


def _title(lay: Layout, result: QAResult) -> list[Flowable]:
    config = result.config
    facts = _cruise_facts(config)
    heading = [
        Paragraph(f"SBE 9/911plus QA report: {config.cruise_id}", lay.title),
        Paragraph(config.cruise_name or "", lay.body),
    ]
    if lay.theme.logo is not None:
        # The title and name beside the logo, right-aligned
        logo = _logo(lay.theme.logo)
        heading = [
            Table(
                [[heading, logo]],
                colWidths=[lay.frame_width - logo.drawWidth - 6, logo.drawWidth + 6],
                style=[
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ],
            )
        ]
    return [
        *heading,
        Spacer(1, 4),
        *([Paragraph(" · ".join(facts), lay.small)] if facts else []),
        Paragraph(
            f"Generated {result.finished:%Y-%m-%d %H:%M} UTC by "
            + ", ".join(f"{k} {v}" for k, v in result.versions.items()),
            lay.small,
        ),
        Paragraph(f"Source: {result.fileset.root}", lay.small),
        Spacer(1, 10),
    ]


def _checks_table(lay: Layout, checks) -> Table:
    rows = [[c.name, c.status, c.value, c.limit, c.detail] for c in checks]
    styles = [
        ("BACKGROUND", (1, i), (1, i), _status_color(lay, c.status))
        for i, c in enumerate(checks, start=1)
    ]
    return table(
        lay,
        ["Check", "Status", "Value", "Limit", "Detail"],
        rows,
        [0.27, 0.08, 0.18, 0.12, 0.35],
        styles,
    )


def _overview(lay: Layout, result: QAResult, svgs: dict[str, Path]) -> list[Flowable]:
    rows, styles = [], []
    for i, data in enumerate(result.casts, start=1):
        checks = result.integrity.casts.get(data.name, []) + result.science.get(data.name, [])
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
        styles.append(("BACKGROUND", (4, i), (4, i), _status_color(lay, status)))
    return [
        Paragraph("Fileset overview", lay.heading),
        _drawing(svgs["map"], lay.frame_width, max_height=lay.fit(MAP_HEIGHT * inch)),
        Spacer(1, 10),
        table(
            lay,
            ["Cast", "Scans", "Max P [dbar]", "Dual T/C", "Pass / warn / fail", "Notes"],
            rows,
            [0.22, 0.09, 0.11, 0.08, 0.16, 0.34],
            styles,
        ),
        Paragraph("Pass / warn / fail counts the integrity and science checks.", lay.small),
        Spacer(1, 12),
        KeepTogether(
            [
                Paragraph("Integrity checks", lay.subheading),
                _checks_table(lay, result.integrity.fileset),
            ]
        ),
    ]


def _sensors(lay: Layout, result: QAResult, data) -> Flowable:
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
            styles.append(("BACKGROUND", (4, i), (4, i), _status_color(lay, WARN)))
    return KeepTogether(
        [
            Paragraph("Sensors", lay.subheading),
            Paragraph(instrument_settings(data), lay.small),
            Spacer(1, 4),
            table(
                lay,
                ["Channel", "Sensor", "Serial number", "Calibrated", "Age at cast [days]"],
                rows,
                [0.24, 0.33, 0.15, 0.14, 0.14],
                styles,
            ),
            Paragraph(f"Ages over {limit} days are highlighted.", lay.small),
        ]
    )


def _one_decimal(value: float) -> str:
    """One decimal place, without a "-0.0" for small negatives"""
    return f"{round(value, 1) + 0.0:.1f}"


def _bottle_table(lay: Layout, data) -> Table | None:
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
    return table(
        lay,
        ["Bottle", "Fire order", "Time (UTC)", "Pressure, mean [dbar]", "Range [dbar]"],
        rows,
        [0.12, 0.14, 0.3, 0.2, 0.24],
    )


def _height(lay: Layout, flowable) -> float:
    _, height = flowable.wrap(lay.frame_width, lay.frame_height)
    return height + flowable.getSpaceBefore() + flowable.getSpaceAfter()


def _bottle_page(lay: Layout, data, svgs: dict[str, Path]) -> list[Flowable]:
    """The bottle fire table with the pressure record below it, sized to the space left. A table
    too long to leave room for a readable plot (very large rosettes) pushes it to the next page
    """
    name = data.name
    bottles = _bottle_table(lay, data)
    title = Paragraph(f"{name}: bottle fires and pressure record", lay.heading)
    subtitle = Paragraph(f"Bottle fires ({bottles._nrows - 1})", lay.subheading)
    gap = 8
    remaining = (
        lay.frame_height
        - _height(lay, title)
        - _height(lay, subtitle)
        - _height(lay, bottles)
        - gap
        - 4
    )
    pressure = svgs[f"{name}/pressure"]
    chart_height = lay.fit(CHART_HEIGHT)
    if remaining >= MIN_CHART_HEIGHT:
        chart = [
            Spacer(1, gap),
            _drawing(pressure, lay.frame_width, min(remaining, chart_height)),
        ]
    else:
        chart = [PageBreak(), _drawing(pressure, lay.frame_width, chart_height)]
    return [title, subtitle, bottles, *chart]


def _cast(lay: Layout, result: QAResult, data, svgs: dict[str, Path]) -> list[Flowable]:
    """Per cast: sensors, integrity and science checks; then the pressure record (with the
    bottle fire table when bottles were fired); then the TS diagram, which always follows the
    pressure record; then profiles. Without bottle fires, the pressure record and TS diagram
    share a page
    """
    name = data.name
    tables = [
        PageBreak(),
        Paragraph(name, lay.heading),
        _sensors(lay, result, data),
        Spacer(1, 6),
        KeepTogether(
            [
                Paragraph("Integrity checks", lay.subheading),
                _checks_table(lay, result.integrity.casts[name]),
            ]
        ),
        Spacer(1, 6),
        KeepTogether(
            [
                Paragraph("Science checks", lay.subheading),
                _checks_table(lay, result.science[name]),
            ]
        ),
    ]
    width = lay.frame_width
    profiles = [
        PageBreak(),
        Paragraph(f"{name}: profiles", lay.heading),
        _drawing(svgs[f"{name}/profiles"], width, max_height=lay.fit(PROFILES_HEIGHT * inch)),
    ]
    if bottle_fires(data):
        return [
            *tables,
            PageBreak(),
            *_bottle_page(lay, data, svgs),
            PageBreak(),
            Paragraph(f"{name}: TS diagram", lay.heading),
            _drawing(svgs[f"{name}/ts"], width, max_height=lay.fit(TS_HEIGHT * inch)),
            *profiles,
        ]
    # Both figures shrink together when the frame is too short for both
    shared = (SHARED_PRESSURE_HEIGHT + SHARED_TS_HEIGHT) * inch
    shrink = min(1.0, lay.fit(shared + 6) / (shared + 6))
    return [
        *tables,
        PageBreak(),
        Paragraph(f"{name}: pressure record and TS diagram", lay.heading),
        _drawing(
            svgs[f"{name}/pressure"], width, max_height=SHARED_PRESSURE_HEIGHT * inch * shrink
        ),
        Spacer(1, 6),
        _drawing(svgs[f"{name}/ts"], width, max_height=SHARED_TS_HEIGHT * inch * shrink),
        *profiles,
    ]


# Pages an integration puts after the title (e.g. R2R's certificate), built from the PDF's Layout
FrontPages = Callable[[Layout], list[Flowable]]


def write_pdf(
    result: QAResult,
    path: Path | str,
    plots_dir: Path | str | None = None,
    front: FrontPages | None = None,
    subject: str = "Integrity and science checks",
    theme: Theme = DEFAULT_THEME,
) -> Path:
    """Writes the PDF, and the SVG plots it embeds into `plots_dir` (default:
    <pdf directory>/<identifier>_plots). `front` makes the pages an integration puts between
    the title and the fileset overview, e.g. R2R's certificate
    """
    path = Path(path)
    plots_dir = Path(plots_dir) if plots_dir else path.parent / f"{result.config.identifier}_plots"
    svgs = write_plot_svgs(result, plots_dir, theme)
    lay = layout(theme)

    title = f"SBE 9/911plus QA report: {result.config.identifier}"
    footer = theme.footer or title
    if theme.organization:
        footer = f"{theme.organization} · {footer}"

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(lay.font, lay.small.fontSize)
        canvas.setFillColor(colors.HexColor(lay.colors.footer_text))
        canvas.drawString(lay.margin, 0.4 * inch, footer)
        canvas.drawRightString(lay.page[0] - lay.margin, 0.4 * inch, f"page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path),
        pagesize=lay.page,
        leftMargin=lay.margin,
        rightMargin=lay.margin,
        topMargin=lay.margin,
        bottomMargin=lay.margin,
        title=title,
        subject=subject,
        creator=f"sbe-qa-processing {result.versions['sbe-qa-processing']}",
    )
    story = _title(lay, result) + (front(lay) if front else []) + _overview(lay, result, svgs)
    for data in result.science_casts:
        story += _cast(lay, result, data, svgs)
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path
