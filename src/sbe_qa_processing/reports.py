"""Writes the core reports for a QA result (the PDF with its SVG plots, and the notebook), and
summarizes the result for the command line. Integrations add their own pages and cells.
"""

from pathlib import Path

from sbe_qa_processing import qa
from sbe_qa_processing.notebook import NotebookExtras, write_notebook
from sbe_qa_processing.pdf_report import FrontPages, write_pdf
from sbe_qa_processing.qa import QAResult
from sbe_qa_processing.theme import resolve_theme


def report_paths(output: Path, identifier: str) -> dict[str, Path]:
    return {
        "pdf": output / f"{identifier}_qa_report.pdf",
        "plots": output / f"{identifier}_plots",
        "notebook": output / f"{identifier}_qa.ipynb",
    }


def write_reports(
    result: QAResult,
    output: Path,
    execute_notebook: bool = False,
    pdf_front: FrontPages | None = None,
    pdf_subject: str | None = None,
    notebook_extras: NotebookExtras | None = None,
    theme_file: Path | None = None,
) -> list[Path]:
    """The PDF, its SVG plots and the notebook, in output, in the theme from theme_file (a
    --theme file) and the config's [theme]
    """
    output.mkdir(parents=True, exist_ok=True)
    paths = report_paths(output, result.config.identifier)
    subject = {"subject": pdf_subject} if pdf_subject else {}
    theme = resolve_theme(theme_file, result.config)
    return [
        write_pdf(result, paths["pdf"], paths["plots"], front=pdf_front, theme=theme, **subject),
        paths["plots"],
        write_notebook(
            result,
            paths["notebook"],
            output,
            execute=execute_notebook,
            extras=notebook_extras,
            theme_file=theme_file,
        ),
    ]


def summary(result: QAResult) -> list[str]:
    """Indented lines on the integrity and science checks that warned or failed"""
    return qa.summary(result)
