"""What the commands share, core's and the integrations': refusing to overwrite, writing a
cruise TOML or a summary, and the report-output arguments. The conventions are in cli.py.
"""

import argparse
import sys
from pathlib import Path

from sbe_qa_processing.filenames import report_stem

STDOUT = "-"


def print_summary(lines: list[str], written: list[Path]) -> None:
    for line in lines:
        print(line)
    for path in written:
        print(f"wrote {path}")


def refuse_existing_reports(paths: dict[str, Path], force: bool) -> bool:
    existing = [path for path in paths.values() if path.exists()]
    if existing and not force:
        print(f"{existing[0]} exists; use --force to overwrite the reports", file=sys.stderr)
        return True
    return False


def refuse_overwrite(path: Path, force: bool) -> bool:
    if str(path) != STDOUT and path.exists() and not force:
        print(f"{path} exists; use --force to overwrite it", file=sys.stderr)
        return True
    return False


def write_text(text: str, path: Path) -> None:
    """text into path, or to stdout for -"""
    if str(path) == STDOUT:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}", file=sys.stderr)


def cruise_toml_path(output: Path, cruise_id: str) -> Path:
    """<output>/<cruise_id>.toml, the cruise ID made safe as a file name"""
    return output / f"{report_stem(cruise_id)}.toml"


def write_cruise_toml(text: str, cruise_id: str, args: argparse.Namespace) -> int:
    """text as <output>/<cruise_id>.toml, or to stdout for -o -"""
    destination = args.output
    if str(destination) != STDOUT:
        destination = cruise_toml_path(destination, cruise_id)
    if refuse_overwrite(destination, args.force):
        return 1
    write_text(text, destination)
    return 0


def add_cruise_toml_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("configs"),
        help="directory for <CRUISE_ID>.toml, or - for stdout (default %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing cruise TOML")


def add_theme(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help="theme file for the PDF, figures and notebook; the config's [theme] applies on top",
    )


def add_report_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("output"),
        help="directory for the reports (default %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing reports")
    parser.add_argument(
        "--execute-notebook", action="store_true", help="run the notebook and save its outputs"
    )
    add_theme(parser)
