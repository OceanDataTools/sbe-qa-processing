"""Report themes: the default reproduces the original look; a theme file and a config's [theme]
change the PDF, its figures and the notebook.
"""

import tomllib
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import nbformat
import pytest
from conftest import CONFIGS
from PIL import Image

from sbe_qa_processing import maps, plots
from sbe_qa_processing.cli import main
from sbe_qa_processing.config import QAConfig, config_to_toml, load_config
from sbe_qa_processing.integrations.r2r.config import (
    config_to_toml as cruise_config_to_toml,
)
from sbe_qa_processing.integrations.r2r.config import load_cruise_config
from sbe_qa_processing.pdf_report import layout
from sbe_qa_processing.theme import (
    COLORBLIND,
    DEFAULT_THEME,
    Colors,
    Theme,
    ThemeError,
    load_theme,
    resolve_theme,
)

TTF = Path(matplotlib.get_data_path()) / "fonts" / "ttf"


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    """No Natural Earth downloads: maps are drawn without coastlines"""
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


def test_default_theme_is_the_original_look():
    colors = DEFAULT_THEME.colors
    assert (colors.primary, colors.secondary) == ("#1f5f8b", "#d1495b")
    assert colors.status == {
        "pass": "#d8f0df",
        "warn": "#fff1bf",
        "fail": "#f6d0cc",
        "info": "#eef2f7",
        "n/a": "#f2f2f2",
    }
    assert colors.rating["G"] == "#2e8b57" and (colors.land, colors.water) == (
        "#ece6d6",
        "#dbe9f4",
    )
    lay = layout()
    assert lay.page == (612.0, 792.0) and lay.margin == pytest.approx(43.2)  # letter, 0.6 in
    assert (lay.font, lay.font_bold) == ("DejaVuSans", "DejaVuSans-Bold")
    sizes = {
        name: (getattr(lay, name).fontSize, getattr(lay, name).leading)
        for name in ("title", "heading", "subheading", "body", "small", "cell", "banner")
    }
    assert sizes == {
        "title": (18, 22),
        "heading": (14, 18),
        "subheading": (12, 14),
        "body": (9, 11),
        "small": (7, 8.5),
        "cell": (7, 8.4),
        "banner": (15, 18),
    }


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "text, problem",
    [
        ("colour = 1\n", "unknown theme settings: colour"),
        ("[colors]\nprimry = '#000000'\n", "unknown theme colors: primry"),
        ("[colors]\nprimary = 'blue'\n", "isn't a #rrggbb color"),
        ("page_size = 'legal'\n", "page_size must be one of letter, A4"),
        ("palette = 'neon'\n", "palette must be one of default, colorblind"),
        ("font_scale = 0\n", "font_scale must be a positive number"),
        ("logo = 'missing.png'\n", "logo"),
    ],
)
def test_bad_theme_settings(tmp_path, text, problem):
    with pytest.raises(ThemeError, match=problem):
        load_theme(write(tmp_path / "theme.toml", text))
    # the same in a config's [theme] table
    with pytest.raises(ThemeError, match=problem):
        load_config(
            write(
                tmp_path / "config.toml", "[theme]\n" + text.replace("[colors]", "[theme.colors]")
            )
        )


def test_paths_are_relative_to_the_toml(tmp_path):
    (tmp_path / "brand").mkdir()
    Image.new("RGB", (40, 20), "navy").save(tmp_path / "brand" / "logo.png")
    theme = load_theme(write(tmp_path / "theme.toml", "logo = 'brand/logo.png'\n"))
    assert theme.logo == tmp_path / "brand" / "logo.png"
    config = load_config(write(tmp_path / "c.toml", "[theme]\nlogo = 'brand/logo.png'\n"))
    assert config.theme == {"logo": str((tmp_path / "brand" / "logo.png").resolve())}


def test_config_applies_over_the_theme_file(tmp_path):
    house = write(
        tmp_path / "house.toml",
        "page_size = 'A4'\norganization = 'House'\npalette = 'colorblind'\n"
        "[colors]\nwater = '#000001'\n",
    )
    config = load_config(
        write(
            tmp_path / "c.toml",
            "[theme]\norganization = 'Cruise'\n[theme.colors]\nland = '#000002'\n",
        )
    )
    theme = resolve_theme(house, config)
    assert (theme.page_size, theme.organization) == ("A4", "Cruise")
    # the palette replaces the colors; [colors] then changes single ones
    assert theme.colors.primary == COLORBLIND.primary
    assert (theme.colors.water, theme.colors.land) == ("#000001", "#000002")
    assert resolve_theme(None, QAConfig()) == DEFAULT_THEME


def test_theme_round_trips_through_config_toml(tmp_path):
    config = load_config(
        write(
            tmp_path / "c.toml",
            "[cruise]\nid = 'LAKE1'\n[theme]\npage_size = 'A4'\nfont_scale = 1.1\n"
            "[theme.colors]\nprimary = '#123456'\n",
        )
    )
    text = config_to_toml(config)
    assert tomllib.loads(text)["theme"] == {
        "page_size": "A4",
        "font_scale": 1.1,
        "colors": {"primary": "#123456"},
    }
    assert load_config(write(tmp_path / "again.toml", text)).theme == config.theme

    # an R2R cruise TOML keeps it too
    cruise = load_cruise_config(CONFIGS / "SP2613.toml")
    cruise.theme = config.theme
    again = write(tmp_path / "cruise.toml", cruise_config_to_toml(cruise))
    assert load_cruise_config(again).theme == config.theme


def svg_of(figure, path: Path) -> str:
    figure.savefig(path, format="svg")
    plt.close(figure)
    return path.read_text()


def test_figures_use_the_theme(tmp_path):
    theme = Theme(
        font=TTF / "DejaVuSerif.ttf",
        figure_font_scale=1.5,
        colors=Colors(water="#010203"),
    )
    svg = svg_of(plots.cast_map([], QAConfig(), theme), tmp_path / "map.svg")
    assert "#010203" in svg and "DejaVuSerif" in svg
    default = svg_of(plots.cast_map([], QAConfig()), tmp_path / "default.svg")
    assert "#010203" not in default and "DejaVuSerif" not in default


def test_themed_run(tmp_path, capsys):
    fileset = tmp_path / "MY_CASTS"
    fileset.mkdir()
    Image.new("RGB", (300, 100), "navy").save(tmp_path / "logo.png")
    house = write(
        tmp_path / "house.toml",
        f"page_size = 'A4'\nfont = '{TTF / 'DejaVuSerif.ttf'}'\n"
        f"bold_font = '{TTF / 'DejaVuSerif-Bold.ttf'}'\nlogo = 'logo.png'\n"
        "organization = 'Example Institution'\n",
    )
    output = tmp_path / "output"
    assert main(["run", str(fileset), "--theme", str(house), "-o", str(output)]) == 0
    pdf = (output / "MY_CASTS_qa_report.pdf").read_bytes()
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in pdf  # A4
    assert b"DejaVuSerif" in pdf and b"DejaVuSans" not in pdf
    assert b"/Subtype /Image" in pdf  # the logo
    # The notebook re-creates the theme
    cells = nbformat.read(output / "MY_CASTS_qa.ipynb", as_version=4).cells
    code = "\n".join(cell.source for cell in cells)
    # The notebook writes the path as a Python literal, so Windows backslashes are doubled
    assert repr(str(house)) in code and "theme=theme" in code


def test_bad_theme_is_reported_without_a_traceback(tmp_path, capsys):
    fileset = tmp_path / "MY_CASTS"
    fileset.mkdir()
    house = write(tmp_path / "house.toml", "page_size = 'legal'\n")
    assert main(["run", str(fileset), "--theme", str(house), "-o", str(tmp_path / "out")]) == 1
    assert "page_size must be one of letter, A4" in capsys.readouterr().err
