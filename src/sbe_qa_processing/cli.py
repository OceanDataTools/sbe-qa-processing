"""Command line interface: sbe-qa-processing run | openvdm | config-from-r2r |
config-from-openvdm | site-config
"""

import argparse
import logging
import sys
import warnings
from pathlib import Path

from sbe_qa_processing.config import config_toml_from_r2r_qa


def _write_reports(result, output: Path, execute_notebook: bool) -> list[Path]:
    """The XML, PDF (with its SVG plots) and notebook for a QA result, in output"""
    from sbe_qa_processing.notebook import write_notebook
    from sbe_qa_processing.pdf_report import write_pdf
    from sbe_qa_processing.xml_report import write_qa_xml

    output.mkdir(parents=True, exist_ok=True)
    stem = result.config.identifier
    plots_dir = output / f"{stem}_plots"
    return [
        write_qa_xml(result, output / f"{stem}_qa.2.0.xml"),
        write_pdf(result, output / f"{stem}_qa_report.pdf", plots_dir),
        plots_dir,
        write_notebook(result, output / f"{stem}_qa.ipynb", output, execute=execute_notebook),
    ]


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


def _run(args: argparse.Namespace) -> int:
    from sbe_qa_processing.qa import run_qa

    result = run_qa(args.config, args.fileset)
    _summarize(result, _write_reports(result, args.output, args.execute_notebook))
    return 0


def _resolve_openvdm(args: argparse.Namespace):
    """The current cruise's HookRun from OpenVDM and the site config, and OpenVDM's web root"""
    from sbe_qa_processing import openvdm

    site_root = args.site_root or openvdm.site_root_from_config(args.openvdm_config)
    cruise_config = openvdm.fetch_cruise_config(site_root)
    site = openvdm.load_site_config(args.site_config)
    fileset_id = str(args.fileset_id or "")
    return openvdm.resolve(cruise_config, site, args.transfer, fileset_id), site_root


def _refuse_overwrite(path: Path, force: bool) -> bool:
    if str(path) != "-" and path.exists() and not force:
        print(f"{path} exists; use --force to overwrite it", file=sys.stderr)
        return True
    return False


def _write_text(text: str, path: Path) -> None:
    """text into path, or to stdout for -"""
    if str(path) == "-":
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path}", file=sys.stderr)


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

    print(
        f"{run.config.cruise_id}: {run.fileset_dir} -> {run.output_dir} "
        f"(cruise extent from {run.extent_source})"
    )
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
    text = config_toml_from_r2r_qa(args.qa_xml)
    if args.output:
        args.output.write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text, end="")
    return 0


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing import openvdm

    try:
        run, site_root = _resolve_openvdm(args)
    except openvdm.OpenVDMError as error:
        print(f"sbe-qa-processing: {error}", file=sys.stderr)
        return 1
    destination = args.output
    if str(destination) != "-":
        destination = destination / f"{run.config.cruise_id}.toml"
    if _refuse_overwrite(destination, args.force):
        return 1
    print(f"{run.config.cruise_id}: cruise extent from {run.extent_source}", file=sys.stderr)
    for note in run.notes:
        print(f"  note: {note}", file=sys.stderr)
    _write_text(openvdm.cruise_toml(run, site_root), destination)
    return 0


def _site_config(args: argparse.Namespace) -> int:
    from sbe_qa_processing.config import fetch_r2r_records
    from sbe_qa_processing.openvdm import SiteConfig, load_site_config, site_config_to_toml
    from sbe_qa_processing.site_setup import prompt_site_config, site_from_r2r

    if _refuse_overwrite(args.output, args.force):
        return 1
    # Re-running over an existing file starts from its values
    existing = str(args.output) != "-" and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    if args.from_r2r:
        cruises = fetch_r2r_records("cruise", args.from_r2r)
        if not cruises:
            print(f"R2R has no cruise {args.from_r2r}", file=sys.stderr)
            return 1
        defaults = site_from_r2r(cruises[0], defaults)
        print(f"Vessel and R2R IDs from R2R's cruise {args.from_r2r}", file=sys.stderr)
    _write_text(site_config_to_toml(prompt_site_config(defaults)), args.output)
    return 0


def _add_openvdm_arguments(parser: argparse.ArgumentParser) -> None:
    """Where to find OpenVDM and the site config, shared by the hook and config-from-openvdm"""
    parser.add_argument(
        "transfer",
        nargs="?",
        default="CTD",
        help="collection system transfer name, e.g. {collectionSystemTransferName} (default CTD)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--openvdm-config",
        type=Path,
        default=Path("/opt/openvdm/server/etc/openvdm.yaml"),
        help="openvdm.yaml, for OpenVDM's siteRoot (default %(default)s)",
    )
    source.add_argument("--site-root", help="OpenVDM's web root URL, instead of --openvdm-config")
    parser.add_argument(
        "--site-config", type=Path, help="optional per-ship TOML (vessel, extent, ...)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sbe-qa-processing",
        description="QA reports (PDF, notebook, R2R QA 2.0 XML) for SBE 9/911plus CTD filesets",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="assess a fileset and write the three reports")
    run.add_argument("config", type=Path, help="cruise TOML")
    run.add_argument("fileset", type=Path, help="BagIt bag or directory of casts")
    run.add_argument("-o", "--output", type=Path, default=Path("output"), help="output directory")
    run.add_argument(
        "--execute-notebook", action="store_true", help="run the notebook and save its outputs"
    )
    run.set_defaults(handler=_run)

    hook = commands.add_parser(
        "openvdm",
        help="assess the current cruise's CTD data from an OpenVDM post-transfer hook",
        description=(
            "Reads the current cruise from OpenVDM's API, assesses the collection system "
            "transfer's CTD files, and writes the reports into an OpenVDM extra directory."
        ),
    )
    _add_openvdm_arguments(hook)
    hook.add_argument("--fileset-id", help="R2R fileset ID, when known")
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
        "config-from-r2r", help="make a cruise TOML from an existing R2R QA 2.0 XML"
    )
    from_r2r.add_argument("qa_xml", type=Path)
    from_r2r.add_argument("-o", "--output", type=Path, help="write here instead of stdout")
    from_r2r.set_defaults(handler=_config_from_r2r)

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
        "--fileset-id", type=int, metavar="FILESET_ID", help="R2R fileset ID, when known"
    )
    from_openvdm.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("configs"),
        help="directory for <CRUISE_ID>.toml, or - for stdout (default %(default)s)",
    )
    from_openvdm.add_argument(
        "--force", action="store_true", help="overwrite an existing cruise TOML"
    )
    from_openvdm.set_defaults(handler=_config_from_openvdm)

    site = commands.add_parser(
        "site-config",
        help="make the per-ship site TOML for the openvdm hook, by prompting for each field",
        description=(
            "Prompts for the per-ship settings OpenVDM doesn't store: vessel and R2R IDs, "
            "report contact, OpenVDM extra directories and a fallback cruise bounding box. "
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
        type=Path,
        default=Path("configs/site.toml"),
        help="site TOML to write, or - for stdout (default %(default)s)",
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Render figures off screen; the notebook uses its own inline backend
    import matplotlib

    matplotlib.use("Agg")
    # seabirdscientific's EOS-80 module imports the deprecated seawater package
    warnings.filterwarnings("ignore", "The seawater library is deprecated")
    try:
        return args.handler(args)
    except Exception as error:  # OpenVDM shows the hook's failure message
        if args.command != "openvdm":
            raise
        print(f"sbe-qa-processing: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
