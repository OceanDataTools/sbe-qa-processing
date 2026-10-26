"""Command line interface: ``sbe-qa-processing <command>``.

Conventions shared by every command:
- ``-o/--output`` is a directory, except for site-config, which writes a single file. Commands
  that write one cruise TOML write ``<output>/<CRUISE_ID>.toml``, or to stdout for ``-o -``.
- Existing outputs aren't overwritten without ``--force``, except by the openvdm hook, which
  replaces its reports after each transfer.
- Fileset IDs are integers. ``fileset`` alone never means an ID: the directory is FILESET_DIR.
- The openvdm hook's arguments are called from installed ``openvdm.yaml`` files, so they keep
  their names and behavior.
- An integration's commands are a group named after it (``r2r ...``), except OpenVDM's
  (``openvdm``, ``config-from-openvdm``, ``site-config``). Each integration's ``cli`` module
  adds them (``add_commands``) and lists its expected errors (``EXPECTED_ERRORS``);
  ``clitools`` has what the commands share.
- ``-v/--verbose``, before or after the command, logs more: ``-v`` is INFO and ``-vv`` DEBUG
  (default WARNING). Expected errors (a server that can't be reached, R2R or OpenVDM not having
  what was asked for, a bad theme) are one line on stderr; ``-vv`` adds their traceback.
"""

import argparse
import logging
import sys
import traceback
import warnings
from pathlib import Path

from sbe_qa_processing.clitools import add_report_output, print_summary, refuse_existing_reports
from sbe_qa_processing.config import ConfigError
from sbe_qa_processing.fileset import FilesetError
from sbe_qa_processing.integrations.openvdm import cli as openvdm_cli
from sbe_qa_processing.integrations.r2r import cli as r2r_cli
from sbe_qa_processing.network import NetworkError
from sbe_qa_processing.theme import ThemeError

# The integrations whose commands this CLI adds (each module's add_commands), in help order
INTEGRATIONS = (openvdm_cli, r2r_cli)
# Errors that are a one-line message, not a traceback: the user can act on them; core's and
# each integration's
EXPECTED_ERRORS = (
    NetworkError,
    ConfigError,
    ThemeError,
    FilesetError,
    *(error for integration in INTEGRATIONS for error in integration.EXPECTED_ERRORS),
)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)  # by the number of -v


# Commands ------------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from sbe_qa_processing import reports
    from sbe_qa_processing.qa import resolve_config, run_qa

    config = resolve_config(args.config, args.fileset_dir)
    if refuse_existing_reports(reports.report_paths(args.output, config.identifier), args.force):
        return 1
    result = run_qa(args.fileset_dir, args.config)
    written = reports.write_reports(
        result, args.output, args.execute_notebook, theme_file=args.theme
    )
    lines = [f"{result.config.identifier}: {len(result.casts)} casts", *reports.summary(result)]
    print_summary(lines, written)
    return 0


def _fetch_map_data(args: argparse.Namespace) -> int:
    import cartopy

    from sbe_qa_processing.maps import prefetch

    for layer in prefetch():
        print(f"cached {layer}")
    print(f"in {cartopy.config['data_dir']}")
    return 0


# Arguments -----------------------------------------------------------------------------------


def _add_verbose(parser: argparse.ArgumentParser, default=0) -> None:
    """-v/--verbose on parser and, so it also works after the command, on every subcommand.
    The subcommands' default is SUPPRESS, so they don't reset a -v given before the command
    """
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=default,
        help="log more: -v for INFO, -vv for DEBUG and tracebacks of expected errors",
    )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for command in action.choices.values():
                _add_verbose(command, argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbe-qa-processing",
        description="QA reports (PDF, notebook) for SBE 9/911plus CTD filesets",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run = commands.add_parser(
        "run",
        help="assess a fileset and write the reports",
        description=(
            "Assesses a fileset: integrity and science checks. Writes the PDF, SVG plots and "
            "notebook. For R2R's tests and QA XML, use `r2r run`."
        ),
    )
    run.add_argument(
        "fileset_dir",
        metavar="FILESET_DIR",
        type=Path,
        help="BagIt bag or directory of casts",
    )
    run.add_argument(
        "-c",
        "--config",
        metavar="CONFIG_TOML",
        type=Path,
        help=(
            "config TOML: title, expected area and dates, thresholds, ...; optional. Without "
            "one, the reports are titled after FILESET_DIR"
        ),
    )
    add_report_output(run)
    run.set_defaults(handler=_run)

    # The integrations' commands: this is the one place core's CLI meets them
    for integration in INTEGRATIONS:
        integration.add_commands(commands)

    maps = commands.add_parser(
        "fetch-map-data",
        help="download the Natural Earth map layers, for working offline",
        description=(
            "Caches the Natural Earth layers the report maps use in cartopy's data directory, "
            "e.g. before going to sea."
        ),
    )
    maps.set_defaults(handler=_fetch_map_data)
    _add_verbose(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    level = LOG_LEVELS[min(args.verbose, len(LOG_LEVELS) - 1)]
    if args.verbose:
        # This package's loggers only: at DEBUG, matplotlib and fontTools would flood the output
        logging.getLogger("sbe_qa_processing").setLevel(level)
    # Render figures off screen; the notebook uses its own inline backend
    import matplotlib

    matplotlib.use("Agg")
    # seabirdscientific's EOS-80 module imports the deprecated seawater package
    warnings.filterwarnings("ignore", "The seawater library is deprecated")
    try:
        return args.handler(args)
    except Exception as error:
        # A command that sets every_error_one_line (the OpenVDM hook, whose failure message
        # OpenVDM shows) reports even an unexpected error in one line
        hook = getattr(args, "every_error_one_line", False)
        if not hook and not isinstance(error, EXPECTED_ERRORS):
            raise
        if level <= logging.DEBUG:
            traceback.print_exc()
        message = f"{type(error).__name__}: {error}" if hook else str(error)
        print(f"sbe-qa-processing: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
