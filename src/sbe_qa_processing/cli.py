"""Command line interface: ``sbe-qa-processing <command>``.

Conventions shared by every command:
- ``-o/--output`` is a directory, except for site-config, which writes a single file. Commands
  that write one cruise TOML write ``<output>/<CRUISE_ID>.toml``, or to stdout for ``-o -``.
- Existing outputs aren't overwritten without ``--force``, except by the openvdm hook, which
  replaces its reports after each transfer.
- Fileset IDs are integers. ``fileset`` alone never means an ID: the directory is FILESET_DIR.
- The openvdm hook's arguments are called from installed ``openvdm.yaml`` files, so they keep
  their names and behavior.
- An integration's commands are a group named after it (``r2r ...``).
- ``-v/--verbose``, before or after the command, logs more: ``-v`` is INFO and ``-vv`` DEBUG
  (default WARNING). Expected errors (a server that can't be reached, R2R or OpenVDM not having
  what was asked for, a bad theme) are one line on stderr; ``-vv`` adds their traceback.
"""

import argparse
import logging
import sys
import tomllib
import traceback
import warnings
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing.filenames import report_stem
from sbe_qa_processing.fileset import FilesetError

# Stdlib only: pyyaml (the openvdm extra) is imported when openvdm.yaml is read
from sbe_qa_processing.integrations.openvdm.hook import DEFAULT_OPENVDM_CONFIG, OpenVDMError
from sbe_qa_processing.integrations.r2r.catalog import R2RError
from sbe_qa_processing.network import NetworkError
from sbe_qa_processing.theme import ThemeError

logger = logging.getLogger(__name__)
STDOUT = "-"
# Errors that are a one-line message, not a traceback: the user can act on them
EXPECTED_ERRORS = (NetworkError, R2RError, OpenVDMError, ThemeError, FilesetError)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)  # by the number of -v


def _print_summary(lines: list[str], written: list[Path]) -> None:
    for line in lines:
        print(line)
    for path in written:
        print(f"wrote {path}")


def _refuse_existing_reports(paths: dict[str, Path], force: bool) -> bool:
    existing = [path for path in paths.values() if path.exists()]
    if existing and not force:
        print(f"{existing[0]} exists; use --force to overwrite the reports", file=sys.stderr)
        return True
    return False


def _refuse_overwrite(path: Path, force: bool) -> bool:
    if str(path) != STDOUT and path.exists() and not force:
        print(f"{path} exists; use --force to overwrite it", file=sys.stderr)
        return True
    return False


def _write_text(text: str, path: Path) -> None:
    """text into path, or to stdout for -"""
    if str(path) == STDOUT:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}", file=sys.stderr)


def _cruise_toml_path(output: Path, cruise_id: str) -> Path:
    """<output>/<cruise_id>.toml, the cruise ID made safe as a file name"""
    return output / f"{report_stem(cruise_id)}.toml"


def _write_cruise_toml(text: str, cruise_id: str, args: argparse.Namespace) -> int:
    """text as <output>/<cruise_id>.toml, or to stdout for -o -"""
    destination = args.output
    if str(destination) != STDOUT:
        destination = _cruise_toml_path(destination, cruise_id)
    if _refuse_overwrite(destination, args.force):
        return 1
    _write_text(text, destination)
    return 0


# Commands ------------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from sbe_qa_processing import reports
    from sbe_qa_processing.qa import resolve_config, run_qa

    config = resolve_config(args.config, args.fileset_dir)
    if _refuse_existing_reports(reports.report_paths(args.output, config.identifier), args.force):
        return 1
    result = run_qa(args.fileset_dir, args.config)
    written = reports.write_reports(
        result, args.output, args.execute_notebook, theme_file=args.theme
    )
    lines = [f"{result.config.identifier}: {len(result.casts)} casts", *reports.summary(result)]
    _print_summary(lines, written)
    return 0


def _r2r_run(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r import reports
    from sbe_qa_processing.integrations.r2r.config import load_cruise_config

    identifier = load_cruise_config(args.cruise_toml).identifier
    if _refuse_existing_reports(reports.report_paths(args.output, identifier), args.force):
        return 1
    run = reports.run_r2r(args.fileset_dir, args.cruise_toml)
    written = reports.write_r2r_reports(
        run, args.output, args.execute_notebook, theme_file=args.theme
    )
    _print_summary(reports.summary(run), written)
    return 0


def _resolve_openvdm(args: argparse.Namespace):
    """The current cruise's HookRun from OpenVDM and the site config, and OpenVDM's web root"""
    from sbe_qa_processing.integrations.openvdm import hook as openvdm

    if args.site_root:
        # No openvdm.yaml: resolve reads the vessel block from the cruise's ovdmConfig.json
        site_root, vessel = args.site_root, None
    else:
        settings = openvdm.load_openvdm_yaml(args.openvdm_config)
        site_root = openvdm.site_root_from_config(settings)
        vessel = openvdm.vessel_settings(settings.get("vessel"), "openvdm.yaml")
    cruise_config = openvdm.fetch_cruise_config(site_root)
    site = openvdm.load_site_config(args.site_config)
    run = openvdm.resolve(cruise_config, site, args.transfer, vessel)
    return run, site_root


def _sources(run) -> str:
    return f"cruise extent from {run.extent_source}, vessel settings from {run.vessel_source}"


def _openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing import reports
    from sbe_qa_processing.integrations.openvdm import hook as openvdm
    from sbe_qa_processing.qa import run_qa

    if args.fileset_id:
        print(
            "warning: --fileset-id is ignored: the hook writes the core reports, not R2R's",
            file=sys.stderr,
        )
    if args.changed_files is not None and not openvdm.has_ctd_changes(args.changed_files):
        print(f"{args.transfer}: no new or updated CTD files; nothing to do")
        return 0

    run, site_root = _resolve_openvdm(args)
    if not run.fileset_dir.is_dir():
        print(f"{run.fileset_dir} doesn't exist yet; nothing to do")
        return 0

    print(f"{run.config.cruise_id}: {run.fileset_dir} -> {run.output_dir} ({_sources(run)})")
    for note in run.notes:
        print(f"  note: {note}")
    run.output_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot what OpenVDM supplied, so the notebook can re-run exactly this assessment
    snapshot = run.output_dir / f"{report_stem(run.config.identifier)}_cruise.toml"
    snapshot.write_text(openvdm.cruise_toml(run, site_root))
    result = run_qa(run.fileset_dir, snapshot)
    written = [
        snapshot,
        *reports.write_reports(
            result, run.output_dir, args.execute_notebook, theme_file=args.theme
        ),
    ]
    openvdm.chown_tree(run.output_dir, run.owner)
    lines = [f"{result.config.identifier}: {len(result.casts)} casts", *reports.summary(result)]
    _print_summary(lines, written)
    _queue_md5_update(args, run, written)
    return 0


def _queue_md5_update(args: argparse.Namespace, run, written: list[Path]) -> None:
    """Adds the reports to OpenVDM's MD5 summary. The QA itself succeeded, so a failure here
    is a warning, not a failed hook
    """
    from sbe_qa_processing.integrations.openvdm import hook as openvdm

    # With --site-root there's no openvdm.yaml to find OpenVDM by; try the default install
    openvdm_yaml = DEFAULT_OPENVDM_CONFIG if args.site_root else args.openvdm_config
    problem = openvdm.queue_md5_update(openvdm.openvdm_install_dir(openvdm_yaml), run, written)
    if problem:
        logger.warning("the reports aren't in OpenVDM's MD5 summary: %s", problem)
    else:
        print(f"queued an MD5 summary update for the reports in {run.output_dir}")


def _r2r_config_from_catalog(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.catalog import config_from_r2r_catalog
    from sbe_qa_processing.integrations.r2r.config import config_to_toml

    destination = _cruise_toml_path(args.output, args.cruise_id)
    if str(args.output) != STDOUT and _refuse_overwrite(destination, args.force):
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
    return _write_cruise_toml(text, config.cruise_id, args)


def _r2r_config_from_qa(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.catalog import config_toml_from_r2r_qa

    text = config_toml_from_r2r_qa(args.r2r_qa_xml)
    return _write_cruise_toml(text, tomllib.loads(text)["cruise"]["id"], args)


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.openvdm import hook as openvdm

    run, site_root = _resolve_openvdm(args)
    print(f"{run.config.cruise_id}: {_sources(run)}", file=sys.stderr)
    for note in run.notes:
        print(f"  note: {note}", file=sys.stderr)
    return _write_cruise_toml(openvdm.cruise_toml(run, site_root), run.config.cruise_id, args)


def _site_config(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.openvdm.hook import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from sbe_qa_processing.integrations.openvdm.site_setup import prompt_site_config

    if _refuse_overwrite(args.output, args.force):
        return 1
    # OpenVDM 2.17's vessel settings win over the site config's, so they aren't asked for
    openvdm_yaml = args.openvdm_config
    if openvdm_yaml is not None and not openvdm_yaml.exists():
        raise OpenVDMError(f"no openvdm.yaml at {openvdm_yaml}; check --openvdm-config")
    if openvdm_yaml is None and DEFAULT_OPENVDM_CONFIG.exists():
        openvdm_yaml = DEFAULT_OPENVDM_CONFIG
    elif openvdm_yaml is None:
        # Not an error: the site TOML may be written away from the OpenVDM server
        print(
            f"No openvdm.yaml at {DEFAULT_OPENVDM_CONFIG}; asking for every field. If OpenVDM "
            "2.17 is installed elsewhere, use --openvdm-config so its vessel settings aren't "
            "asked for.\n",
            file=sys.stderr,
        )
    from_openvdm = {}
    if openvdm_yaml is not None:
        from_openvdm = vessel_settings(load_openvdm_yaml(openvdm_yaml).get("vessel"), "").settings
    # Re-running over an existing file starts from its values
    existing = str(args.output) != STDOUT and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    if defaults.ignored:
        print(f"Dropping {', '.join(defaults.ignored)}: no longer used\n", file=sys.stderr)
    site = prompt_site_config(defaults, from_openvdm=from_openvdm)
    _write_text(site_config_to_toml(site), args.output)
    return 0


def _r2r_fetch_fileset(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.r2r.download import download_fileset

    destination = args.output / f"{args.cruise_id}_{args.fileset_id}_ctd"
    if _refuse_overwrite(destination, args.force):
        return 1
    download = download_fileset(args.cruise_id, args.fileset_id, destination)
    if download.qa_report is None:
        print("R2R has no QA report for this fileset", file=sys.stderr)
    print(f"{download.files} files from {download.bag_url} -> {destination}")
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


def _add_cruise_toml_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("configs"),
        help="directory for <CRUISE_ID>.toml, or - for stdout (default %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing cruise TOML")


def _add_openvdm_arguments(parser: argparse.ArgumentParser) -> None:
    """Where to find OpenVDM and the site config, shared by the hook and config-from-openvdm"""
    parser.add_argument(
        "transfer",
        metavar="TRANSFER",
        nargs="?",
        default="CTD",
        help=(
            "collection system transfer name, e.g. {collectionSystemTransferName} "
            "(default %(default)s)"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        default=DEFAULT_OPENVDM_CONFIG,
        help="openvdm.yaml, for OpenVDM's siteRoot and vessel settings (default %(default)s)",
    )
    source.add_argument(
        "--site-root",
        metavar="URL",
        help=(
            "OpenVDM's web root URL, instead of --openvdm-config; the vessel settings then come "
            "from the cruise's ovdmConfig.json"
        ),
    )
    parser.add_argument(
        "--site-config",
        metavar="SITE_TOML",
        type=Path,
        help="per-ship TOML from site-config (vessel, extent, ...); optional",
    )


def _add_theme(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help="theme file for the PDF, figures and notebook; the config's [theme] applies on top",
    )


def _add_report_output(parser: argparse.ArgumentParser) -> None:
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
    _add_theme(parser)


def _add_r2r_commands(commands) -> None:
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
    _add_report_output(run)
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
    _add_cruise_toml_output(from_catalog)
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
    _add_cruise_toml_output(from_qa)
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
    _add_report_output(run)
    run.set_defaults(handler=_run)

    hook = commands.add_parser(
        "openvdm",
        help="assess the current cruise's CTD data from an OpenVDM post-transfer hook",
        description=(
            "Reads the current cruise from OpenVDM's API, assesses the collection system "
            "transfer's CTD files, and writes the reports into an OpenVDM extra directory, "
            "replacing earlier ones."
        ),
    )
    _add_openvdm_arguments(hook)
    hook.add_argument(
        "--fileset-id",
        metavar="FILESET_ID",
        help="ignored; kept so installed openvdm.yaml hook commands still run",
    )
    hook.add_argument(
        "--changed-files",
        action="append",
        metavar="FILES",
        help=(
            "OpenVDM's {newFiles}/{updatedFiles}; repeatable. When given, the QA only runs if "
            "they include CTD files. Use the --changed-files={newFiles} form, so an empty list "
            "isn't dropped"
        ),
    )
    hook.add_argument(
        "--execute-notebook", action="store_true", help="run the notebook and save its outputs"
    )
    _add_theme(hook)
    hook.set_defaults(handler=_openvdm)

    from_openvdm = commands.add_parser(
        "config-from-openvdm",
        help="make a config TOML from OpenVDM's current cruise, without running the QA",
        description=(
            "Merges OpenVDM's current cruise with the site config, as the openvdm hook does, "
            "and writes the config TOML for review and `run -c`. Reports what's missing or "
            "assumed."
        ),
    )
    _add_openvdm_arguments(from_openvdm)
    _add_cruise_toml_output(from_openvdm)
    from_openvdm.set_defaults(handler=_config_from_openvdm)

    site = commands.add_parser(
        "site-config",
        help="make the per-ship site TOML for the openvdm hook, by prompting for each field",
        description=(
            "Prompts for the per-ship settings OpenVDM doesn't store: the vessel name and "
            "report contact (unless openvdm.yaml has them, from OpenVDM 2.17), the reports' "
            "extra directory and an expected cruise area. Works offline. Over an existing file "
            "(with --force), its values are the defaults."
        ),
    )
    site.add_argument(
        "-o",
        "--output",
        metavar="SITE_TOML",
        type=Path,
        default=Path("configs/site.toml"),
        help="site TOML to write, or - for stdout (default %(default)s)",
    )
    site.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        help=(
            "openvdm.yaml whose vessel settings (OpenVDM 2.17) aren't asked for again (default "
            f"{DEFAULT_OPENVDM_CONFIG}, when it exists)"
        ),
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)

    _add_r2r_commands(commands)

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
        hook = args.command == "openvdm"  # OpenVDM shows the hook's failure message
        if not hook and not isinstance(error, EXPECTED_ERRORS):
            raise
        if level <= logging.DEBUG:
            traceback.print_exc()
        message = f"{type(error).__name__}: {error}" if hook else str(error)
        print(f"sbe-qa-processing: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
