"""Command line interface: ``sbe-qa-processing <command>``.

Conventions shared by every command:
- ``-o/--output`` is a directory, except for site-config, which writes a single file. Commands
  that write one cruise TOML write ``<output>/<CRUISE_ID>.toml``, or to stdout for ``-o -``.
- Existing outputs aren't overwritten without ``--force``, except by the openvdm hook, which
  replaces its reports after each transfer.
- Fileset IDs are integers. ``fileset`` alone never means an ID: the directory is FILESET_DIR.
- The openvdm hook's arguments are called from installed ``openvdm.yaml`` files, so they keep
  their names and behavior.
"""

import argparse
import logging
import sys
import tomllib
import warnings
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing.config import R2RError
from sbe_qa_processing.openvdm import DEFAULT_OPENVDM_CONFIG, OpenVDMError

STDOUT = "-"


def _write_reports(result, output: Path, execute_notebook: bool) -> list[Path]:
    """The XML, PDF (with its SVG plots) and notebook for a QA result, in output"""
    from sbe_qa_processing.notebook import write_notebook
    from sbe_qa_processing.pdf_report import write_pdf
    from sbe_qa_processing.xml_report import write_qa_xml

    output.mkdir(parents=True, exist_ok=True)
    paths = _report_paths(output, result.config.identifier)
    return [
        write_qa_xml(result, paths["xml"]),
        write_pdf(result, paths["pdf"], paths["plots"]),
        paths["plots"],
        write_notebook(result, paths["notebook"], output, execute=execute_notebook),
    ]


def _report_paths(output: Path, identifier: str) -> dict[str, Path]:
    return {
        "xml": output / f"{identifier}_qa.2.0.xml",
        "pdf": output / f"{identifier}_qa_report.pdf",
        "plots": output / f"{identifier}_plots",
        "notebook": output / f"{identifier}_qa.ipynb",
    }


def _summarize(result, written: list[Path]) -> None:
    print(f"{result.config.identifier}: R2R rating {result.r2r.rating}")
    for test in result.r2r.tests:
        failures = f"  ({', '.join(test.failures)})" if test.failures else ""
        print(f"  {test.rating}  {test.name}{failures}")
    problems = [
        f"{cast}: {check.name} {check.status} ({check.value})"
        for cast, checks in result.science.items()
        for check in checks
        if check.status in ("warn", "fail")
    ]
    print(f"  science checks: {len(problems)} warnings/failures")
    for problem in problems:
        print(f"    {problem}")
    for path in written:
        print(f"wrote {path}")


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


def _write_cruise_toml(text: str, cruise_id: str, args: argparse.Namespace) -> int:
    """text as <output>/<cruise_id>.toml, or to stdout for -o -"""
    destination = args.output
    if str(destination) != STDOUT:
        destination = destination / f"{cruise_id}.toml"
    if _refuse_overwrite(destination, args.force):
        return 1
    _write_text(text, destination)
    return 0


# Commands ------------------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    from sbe_qa_processing.config import load_config
    from sbe_qa_processing.qa import run_qa

    identifier = load_config(args.cruise_toml).identifier
    existing = [p for p in _report_paths(args.output, identifier).values() if p.exists()]
    if existing and not args.force:
        print(f"{existing[0]} exists; use --force to overwrite the reports", file=sys.stderr)
        return 1
    result = run_qa(args.cruise_toml, args.fileset_dir)
    _summarize(result, _write_reports(result, args.output, args.execute_notebook))
    return 0


def _resolve_openvdm(args: argparse.Namespace):
    """The current cruise's HookRun from OpenVDM and the site config, and OpenVDM's web root"""
    from sbe_qa_processing import openvdm

    if args.site_root:
        # No openvdm.yaml: resolve reads the vessel block from the cruise's ovdmConfig.json
        site_root, vessel = args.site_root, None
    else:
        settings = openvdm.load_openvdm_yaml(args.openvdm_config)
        site_root = openvdm.site_root_from_config(settings)
        vessel = openvdm.vessel_settings(settings.get("vessel"), "openvdm.yaml")
    cruise_config = openvdm.fetch_cruise_config(site_root)
    site = openvdm.load_site_config(args.site_config)
    fileset_id = str(args.fileset_id or "")
    run = openvdm.resolve(cruise_config, site, args.transfer, fileset_id, vessel)
    return run, site_root


def _sources(run) -> str:
    return f"cruise extent from {run.extent_source}, vessel settings from {run.vessel_source}"


def _openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing import openvdm
    from sbe_qa_processing.qa import run_qa

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
    snapshot = run.output_dir / f"{run.config.identifier}_cruise.toml"
    snapshot.write_text(openvdm.cruise_toml(run, site_root))
    result = run_qa(snapshot, run.fileset_dir)
    written = [snapshot, *_write_reports(result, run.output_dir, args.execute_notebook)]
    openvdm.chown_tree(run.output_dir, run.owner)
    _summarize(result, written)
    return 0


def _config_from_r2r(args: argparse.Namespace) -> int:
    from sbe_qa_processing.config import config_from_r2r_catalog, config_to_toml

    destination = args.output / f"{args.cruise_id}.toml"
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


def _config_from_r2r_qa(args: argparse.Namespace) -> int:
    from sbe_qa_processing.config import config_toml_from_r2r_qa

    text = config_toml_from_r2r_qa(args.r2r_qa_xml)
    return _write_cruise_toml(text, tomllib.loads(text)["cruise"]["id"], args)


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing import openvdm

    run, site_root = _resolve_openvdm(args)
    print(f"{run.config.cruise_id}: {_sources(run)}", file=sys.stderr)
    for note in run.notes:
        print(f"  note: {note}", file=sys.stderr)
    return _write_cruise_toml(openvdm.cruise_toml(run, site_root), run.config.cruise_id, args)


def _site_config(args: argparse.Namespace) -> int:
    from sbe_qa_processing.config import fetch_r2r_records
    from sbe_qa_processing.openvdm import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from sbe_qa_processing.site_setup import prompt_site_config, site_from_r2r

    if _refuse_overwrite(args.output, args.force):
        return 1
    # OpenVDM 2.17's vessel settings win over the site config's, so they aren't asked for
    openvdm_yaml = args.openvdm_config
    if openvdm_yaml is None and DEFAULT_OPENVDM_CONFIG.exists():
        openvdm_yaml = DEFAULT_OPENVDM_CONFIG
    from_openvdm = {}
    if openvdm_yaml is not None:
        from_openvdm = vessel_settings(load_openvdm_yaml(openvdm_yaml).get("vessel"), "").settings
    # Re-running over an existing file starts from its values
    existing = str(args.output) != STDOUT and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    if args.from_r2r:
        cruises = fetch_r2r_records("cruise", args.from_r2r)
        if not cruises:
            raise R2RError(f"R2R has no cruise {args.from_r2r}")
        defaults = site_from_r2r(cruises[0], defaults)
        print(f"Vessel and R2R IDs from R2R's cruise {args.from_r2r}", file=sys.stderr)
    site = prompt_site_config(defaults, from_openvdm=from_openvdm)
    _write_text(site_config_to_toml(site), args.output)
    return 0


def _fetch_fileset(args: argparse.Namespace) -> int:
    from sbe_qa_processing.r2r_download import download_fileset

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbe-qa-processing",
        description="QA reports (PDF, notebook, R2R QA 2.0 XML) for SBE 9/911plus CTD filesets",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run = commands.add_parser(
        "run",
        help="assess a fileset and write the reports",
        description="Assesses a fileset and writes the R2R QA XML, PDF, SVG plots and notebook.",
    )
    run.add_argument("cruise_toml", metavar="CRUISE_TOML", type=Path, help="cruise TOML")
    run.add_argument(
        "fileset_dir",
        metavar="FILESET_DIR",
        type=Path,
        help="BagIt bag or directory of casts",
    )
    run.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("output"),
        help="directory for the reports (default %(default)s)",
    )
    run.add_argument("--force", action="store_true", help="overwrite existing reports")
    run.add_argument(
        "--execute-notebook", action="store_true", help="run the notebook and save its outputs"
    )
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
    hook.add_argument("--fileset-id", metavar="FILESET_ID", help="R2R fileset ID, when known")
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
    hook.set_defaults(handler=_openvdm)

    from_r2r = commands.add_parser(
        "config-from-r2r",
        help="make a cruise TOML from R2R's catalog API",
        description=(
            "Writes a cruise TOML from R2R's catalog API: the cruise's name, vessel, operator, "
            "dates, ports and navigation bounding box, checking that the fileset is one of its "
            "CTD filesets. The API has no port coordinates, country or state; for those, and "
            "for exact parity with an R2R QA report, use config-from-r2r-qa."
        ),
    )
    _add_r2r_ids(from_r2r)
    _add_cruise_toml_output(from_r2r)
    from_r2r.set_defaults(handler=_config_from_r2r)

    from_qa = commands.add_parser(
        "config-from-r2r-qa",
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
    from_qa.set_defaults(handler=_config_from_r2r_qa)

    from_openvdm = commands.add_parser(
        "config-from-openvdm",
        help="make a cruise TOML from OpenVDM's current cruise, without running the QA",
        description=(
            "Merges OpenVDM's current cruise with the site config, as the openvdm hook does, "
            "and writes the cruise TOML for review and `run`. Reports what's missing or assumed."
        ),
    )
    _add_openvdm_arguments(from_openvdm)
    from_openvdm.add_argument(
        "--fileset-id", metavar="FILESET_ID", type=_fileset_id, help="R2R fileset ID, when known"
    )
    _add_cruise_toml_output(from_openvdm)
    from_openvdm.set_defaults(handler=_config_from_openvdm)

    site = commands.add_parser(
        "site-config",
        help="make the per-ship site TOML for the openvdm hook, by prompting for each field",
        description=(
            "Prompts for the per-ship settings OpenVDM doesn't store: vessel and R2R IDs and "
            "report contact (unless openvdm.yaml has them, from OpenVDM 2.17), the reports' "
            "extra directory and a fallback cruise bounding box. "
            "Works offline; --from-r2r pre-fills the vessel and R2R IDs. Over an existing "
            "file (with --force), its values are the defaults."
        ),
    )
    site.add_argument(
        "--from-r2r",
        metavar="CRUISE_ID",
        help="pre-fill vessel, operator and scheduler from a past cruise of the ship in R2R",
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
    fetch.set_defaults(handler=_fetch_fileset)

    maps = commands.add_parser(
        "fetch-map-data",
        help="download the Natural Earth map layers, for working offline",
        description=(
            "Caches the Natural Earth layers the report maps use in cartopy's data directory, "
            "e.g. before going to sea."
        ),
    )
    maps.set_defaults(handler=_fetch_map_data)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Render figures off screen; the notebook uses its own inline backend
    import matplotlib

    matplotlib.use("Agg")
    # seabirdscientific's EOS-80 module imports the deprecated seawater package
    warnings.filterwarnings("ignore", "The seawater library is deprecated")
    try:
        return args.handler(args)
    except Exception as error:
        if args.command == "openvdm":  # OpenVDM shows the hook's failure message
            print(f"sbe-qa-processing: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        if isinstance(error, R2RError | OpenVDMError):  # expected: no traceback
            print(f"sbe-qa-processing: {error}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
