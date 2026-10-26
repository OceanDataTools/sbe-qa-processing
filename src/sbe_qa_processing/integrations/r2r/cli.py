"""The R2R commands, a group under `r2r`: `run`, `config-from-catalog`, `config-from-qa` and
`fetch-fileset`. Core's cli.py adds them with `add_commands`.
"""

import argparse
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing.clitools import (
    STDOUT,
    add_cruise_toml_output,
    add_report_output,
    cruise_toml_path,
    print_summary,
    refuse_existing_reports,
    refuse_overwrite,
    write_cruise_toml,
)
from sbe_qa_processing.integrations.r2r.catalog import R2RError

# Errors that are a one-line message: R2R not having what was asked for
EXPECTED_ERRORS: tuple[type[Exception], ...] = (R2RError,)


def _r2r_run(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r import reports
    from sbe_qa_processing.integrations.r2r.config import load_cruise_config

    identifier = load_cruise_config(args.cruise_toml).identifier
    if refuse_existing_reports(reports.report_paths(args.output, identifier), args.force):
        return 1
    run = reports.run_r2r(args.fileset_dir, args.cruise_toml)
    written = reports.write_r2r_reports(
        run, args.output, args.execute_notebook, theme_file=args.theme
    )
    print_summary(reports.summary(run), written)
    return 0


def _r2r_config_from_catalog(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.catalog import config_from_r2r_catalog
    from sbe_qa_processing.integrations.r2r.config import config_to_toml

    destination = cruise_toml_path(args.output, args.cruise_id)
    if str(args.output) != STDOUT and refuse_overwrite(destination, args.force):
        return 1  # before asking R2R
    config = config_from_r2r_catalog(args.cruise_id, args.fileset_id)
    lines = [f"# Cruise metadata from R2R's catalog API, {datetime.now(UTC):%Y-%m-%d}"]
    if config.extent is None:
        print(
            "warning: R2R has no navigation bounding box for this cruise; add [cruise.extent] "
            "by hand, or the Lat/Lon test is GREY (N)",
            file=sys.stderr,
        )
        lines.append("# R2R has no navigation bounding box for this cruise; add [cruise.extent]")
    text = "\n".join(lines) + "\n" + config_to_toml(config)
    return write_cruise_toml(text, config.cruise_id, args)


def _r2r_config_from_qa(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.catalog import config_toml_from_r2r_qa

    text = config_toml_from_r2r_qa(args.r2r_qa_xml)
    return write_cruise_toml(text, tomllib.loads(text)["cruise"]["id"], args)


def _r2r_fetch_fileset(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.download import download_fileset

    destination = args.output / f"{args.cruise_id}_{args.fileset_id}_ctd"
    if refuse_overwrite(destination, args.force):
        return 1
    download = download_fileset(args.cruise_id, args.fileset_id, destination)
    if download.qa_report is None:
        print("R2R has no QA report for this fileset", file=sys.stderr)
    print(f"{download.files} files from {download.bag_url} -> {destination}")
    return 0


def _fileset_id(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an R2R fileset ID (an integer): {text!r}") from None


def _add_r2r_ids(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("cruise_id", metavar="CRUISE_ID", help="R2R cruise ID, e.g. SP2613")
    parser.add_argument(
        "fileset_id", metavar="FILESET_ID", type=_fileset_id, help="R2R fileset ID, e.g. 169847"
    )


def add_commands(commands) -> None:
    """The `r2r` group and its commands, on core's subparsers"""
    group = commands.add_parser(
        "r2r",
        help="Rolling Deck to Repository: R2R's tests and QA XML, cruise configs, filesets",
        description=(
            "R2R's CTD tests, ratings and QA 2.0 XML, cruise TOMLs from R2R's catalog, and "
            "downloads of released filesets."
        ),
    )
    commands = group.add_subparsers(
        dest="r2r_command", required=True, metavar="COMMAND", help="R2R command"
    )

    run = commands.add_parser(
        "run",
        help="assess a fileset with R2R's tests and write the reports and R2R QA XML",
        description=(
            "Assesses a fileset as `run` does, plus R2R's CTD tests. Writes the R2R QA 2.0 XML, "
            "and the PDF (with R2R's certificate), SVG plots and notebook, named "
            "<CRUISE_ID>_<FILESET_ID>."
        ),
    )
    run.add_argument(
        "cruise_toml",
        metavar="CRUISE_TOML",
        type=Path,
        help="cruise TOML with R2R's IDs, e.g. from `r2r config-from-catalog`",
    )
    run.add_argument(
        "fileset_dir", metavar="FILESET_DIR", type=Path, help="BagIt bag or directory of casts"
    )
    add_report_output(run)
    run.set_defaults(handler=_r2r_run)

    from_catalog = commands.add_parser(
        "config-from-catalog",
        help="make a cruise TOML from R2R's catalog API",
        description=(
            "Writes a cruise TOML from R2R's catalog API: the cruise's name, vessel, operator, "
            "dates, ports and navigation bounding box, checking that the fileset is one of its "
            "CTD filesets. The API has no port coordinates, country or state; for those, and "
            "for exact parity with an R2R QA report, use `r2r config-from-qa`."
        ),
    )
    _add_r2r_ids(from_catalog)
    add_cruise_toml_output(from_catalog)
    from_catalog.set_defaults(handler=_r2r_config_from_catalog)

    from_qa = commands.add_parser(
        "config-from-qa",
        help="make a cruise TOML from an existing R2R QA 2.0 XML",
        description=(
            "Writes a cruise TOML from an R2R QA 2.0 report's fileset info, for checking this "
            "tool's results against R2R's."
        ),
    )
    from_qa.add_argument(
        "r2r_qa_xml", metavar="R2R_QA_XML", type=Path, help="R2R's QA 2.0 XML for the fileset"
    )
    add_cruise_toml_output(from_qa)
    from_qa.set_defaults(handler=_r2r_config_from_qa)

    fetch = commands.add_parser(
        "fetch-fileset",
        help="download a released R2R CTD fileset and R2R's QA report",
        description=(
            "Downloads a released R2R CTD fileset (a BagIt bag) into "
            "<OUTPUT_DIR>/<CRUISE_ID>_<FILESET_ID>_ctd/, with R2R's QA report beside it as "
            "r2r_qa.2.0.xml, e.g. for the validation tests."
        ),
    )
    _add_r2r_ids(fetch)
    fetch.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("data"),
        help="directory for the fileset's directory (default %(default)s)",
    )
    fetch.add_argument(
        "--force", action="store_true", help="download into an existing fileset directory"
    )
    fetch.set_defaults(handler=_r2r_fetch_fileset)
