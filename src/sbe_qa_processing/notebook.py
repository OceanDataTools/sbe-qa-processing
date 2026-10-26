"""Writes a Jupyter notebook that re-runs the QA for a fileset step by step.

The cells are `notebook_template.py`, a Python file Ruff checks, with markers this module fills
for the run; `tests/test_notebook.py` executes the result. Integrations add their imports and
cells through `NotebookExtras` (R2R's tests, certificate and XML), never by core knowing about
them.
"""

import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from sbe_qa_processing.filenames import report_stem
from sbe_qa_processing.qa import QAResult

# The notebook's cells, as Python; see its docstring for the markers
TEMPLATE = files("sbe_qa_processing") / "notebook_template.py"
MARKER = re.compile(r"^.*# @(?P<name>\w+)$")


@dataclass
class NotebookExtras:
    """What an integration adds to the core notebook"""

    heading: str = ""  # after the title, e.g. " fileset 169847"
    reports: str = ""  # what else the reports are, e.g. " and R2R QA XML"
    checks: str = ""  # what else is run, e.g. ", R2R's CTD tests"
    imports: str = ""  # import lines for the setup cell
    config_path: Path | None = None  # CONFIG, when the result has no config file
    load_config: str = ""  # a function the setup cell loads CONFIG with, e.g. load_cruise_config
    after_run: str = ""  # setup cell code after run_qa
    cells: list = field(default_factory=list)  # after the setup cell
    write: str = ""  # report-writing code before the PDF's
    pdf_arguments: str = ""  # more write_pdf arguments, e.g. ", front=..."


def _template() -> list[tuple[str, list[str]]]:
    """notebook_template.py's cells, (kind, lines), in order"""
    cells: list[tuple[str, list[str]]] = []
    for line in TEMPLATE.read_text().splitlines():
        if line.startswith("# %%"):
            cells.append(("markdown" if line == "# %% [markdown]" else "code", []))
        elif cells:
            cells[-1][1].append(line)
    return cells


def _code(lines: list[str], code: dict[str, str]) -> str:
    """A template code cell with its markers filled"""
    out: list[str] = []
    for line in lines:
        if line.startswith("# @raw "):
            out.append(line.removeprefix("# @raw "))
        elif marker := MARKER.match(line):
            if replacement := code[marker["name"]].strip("\n"):
                out.append(replacement)
        elif line or (out and out[-1]):  # an empty marker leaves no second blank line
            out.append(line)
    return "\n".join(out).strip("\n")


def _text(lines: list[str], values: dict[str, str]) -> str:
    """A template markdown cell with its `{name}`s filled"""
    text = "\n".join(line.removeprefix("#").removeprefix(" ") for line in lines).strip("\n")
    for name, value in values.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def build_notebook(
    result: QAResult,
    output_dir: Path,
    extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
) -> nbformat.NotebookNode:
    """The notebook in notebook_template.py, for this run (theme_file is the --theme file the
    reports were written with, if any)
    """
    config = result.config
    extras = extras or NotebookExtras()
    config_path = result.config_path or extras.config_path
    config_path = Path(config_path).resolve() if config_path else None
    config_code = f"{extras.load_config}(CONFIG)" if extras.load_config else "CONFIG"
    pdf = report_stem(config.identifier) + "_qa_report.pdf"
    values = {
        "cruise_id": config.cruise_id,
        "heading": extras.heading,
        "cruise_name": config.cruise_name,
        "reports": extras.reports,
        "checks": extras.checks,
        "version": result.versions["sbe-qa-processing"],
        "seabirdscientific": result.versions["seabirdscientific"],
    }
    code = {
        "config_path": f"CONFIG = Path({str(config_path)!r})" if config_path else "CONFIG = None",
        "fileset": f"FILESET = Path({str(result.fileset.root.resolve())!r})",
        "theme_file": (
            f"THEME_FILE = Path({str(Path(theme_file).resolve())!r})"
            if theme_file
            else "THEME_FILE = None  # a --theme file; the config's [theme] applies on top"
        ),
        "output_dir": f"OUTPUT_DIR = Path({str(Path(output_dir).resolve())!r})",
        "extras_imports": extras.imports,
        "run": f"result = run_qa(FILESET, {config_code})",
        "after_run": extras.after_run,
        "write": extras.write,
        "write_pdf": (
            f"print(write_pdf(result, OUTPUT_DIR / {pdf!r}{extras.pdf_arguments}, theme=theme))"
        ),
    }
    cells = []
    for kind, lines in _template():
        if kind == "markdown":
            cells.append(new_markdown_cell(_text(lines, values)))
        elif [line for line in lines if line.strip()] == ["# @cells"]:
            cells += extras.cells
        else:
            cells.append(new_code_cell(_code(lines, code)))
    notebook = new_notebook(cells=cells)
    notebook.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    notebook.metadata["language_info"] = {"name": "python"}
    return notebook


def write_notebook(
    result: QAResult,
    path: Path | str,
    output_dir: Path | str,
    execute: bool = False,
    extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
) -> Path:
    path = Path(path)
    notebook = build_notebook(result, Path(output_dir), extras, theme_file)
    if execute:
        try:
            from nbclient import NotebookClient
        except ImportError as error:
            raise RuntimeError("executing the notebook needs nbclient and ipykernel") from error
        NotebookClient(
            notebook, timeout=1800, resources={"metadata": {"path": str(path.parent)}}
        ).execute()
    nbformat.write(notebook, path)
    return path
