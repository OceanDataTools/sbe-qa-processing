"""Command line interface: sbe-qa-processing run | openvdm | config-from-r2r"""

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


def _openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing import openvdm
    from sbe_qa_processing.config import config_to_toml
    from sbe_qa_processing.qa import run_qa

    if args.changed_files is not None and not openvdm.has_ctd_changes(args.changed_files):
        print(f"{args.transfer}: no new or updated CTD files; nothing to do")
        return 0

    site_root = args.site_root or openvdm.site_root_from_config(args.openvdm_config)
    cruise_config = openvdm.fetch_cruise_config(site_root)
    site = openvdm.load_site_config(args.site_config)
    run = openvdm.resolve(cruise_config, site, args.transfer, args.fileset_id or "")
    if not run.fileset_dir.is_dir():
        print(f"{run.fileset_dir} doesn't exist yet; nothing to do")
        return 0

    print(
        f"{run.config.cruise_id}: {run.fileset_dir} -> {run.output_dir} "
        f"(cruise extent from {run.extent_source})"
    )
    run.output_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot what OpenVDM supplied, so the notebook can re-run exactly this assessment
    snapshot = run.output_dir / f"{run.config.identifier}_cruise.toml"
    snapshot.write_text(
        f"# Resolved from OpenVDM ({site_root}) for {args.transfer}\n" + config_to_toml(run.config)
    )
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
    hook.add_argument(
        "transfer",
        nargs="?",
        default="CTD",
        help="collection system transfer name, e.g. {collectionSystemTransferName} (default CTD)",
    )
    source = hook.add_mutually_exclusive_group()
    source.add_argument(
        "--openvdm-config",
        type=Path,
        default=Path("/opt/openvdm/server/etc/openvdm.yaml"),
        help="openvdm.yaml, for OpenVDM's siteRoot (default %(default)s)",
    )
    source.add_argument("--site-root", help="OpenVDM's web root URL, instead of --openvdm-config")
    hook.add_argument(
        "--site-config", type=Path, help="optional per-ship TOML (vessel, extent, ...)"
    )
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
