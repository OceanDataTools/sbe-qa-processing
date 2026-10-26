"""The OpenVDM commands: the `openvdm` post-transfer hook, `config-from-openvdm` and
`site-config`. Core's cli.py adds them with `add_commands`. Installed `openvdm.yaml` files call
the hook, so its arguments keep their names; in `openvdm` mode every error is one line, because
OpenVDM displays it (the parser default `every_error_one_line`).
"""

import argparse
import logging
import sys
from pathlib import Path

from sbe_qa_processing.clitools import (
    STDOUT,
    add_cruise_toml_output,
    add_theme,
    print_summary,
    refuse_overwrite,
    write_cruise_toml,
    write_text,
)
from sbe_qa_processing.filenames import report_stem

# Stdlib only: pyyaml (the openvdm extra) is imported when openvdm.yaml is read
from sbe_qa_processing.integrations.openvdm.hook import DEFAULT_OPENVDM_CONFIG, OpenVDMError

logger = logging.getLogger(__name__)
# Errors that are a one-line message: OpenVDM not having what was asked for
EXPECTED_ERRORS: tuple[type[Exception], ...] = (OpenVDMError,)


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
    print_summary(lines, written)
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


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.openvdm import hook as openvdm

    run, site_root = _resolve_openvdm(args)
    print(f"{run.config.cruise_id}: {_sources(run)}", file=sys.stderr)
    for note in run.notes:
        print(f"  note: {note}", file=sys.stderr)
    return write_cruise_toml(openvdm.cruise_toml(run, site_root), run.config.cruise_id, args)


def _site_config(args: argparse.Namespace) -> int:
    from sbe_qa_processing.integrations.openvdm.hook import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from sbe_qa_processing.integrations.openvdm.site_setup import prompt_site_config

    if refuse_overwrite(args.output, args.force):
        return 1
    # OpenVDM 2.17's vessel settings are the defaults where the site config has none
    openvdm_yaml = args.openvdm_config
    if openvdm_yaml is not None and not openvdm_yaml.exists():
        raise OpenVDMError(f"no openvdm.yaml at {openvdm_yaml}; check --openvdm-config")
    if openvdm_yaml is None and DEFAULT_OPENVDM_CONFIG.exists():
        openvdm_yaml = DEFAULT_OPENVDM_CONFIG
    elif openvdm_yaml is None:
        # Not an error: the site TOML may be written away from the OpenVDM server
        print(
            f"No openvdm.yaml at {DEFAULT_OPENVDM_CONFIG}; asking for every field. If OpenVDM "
            "2.17 is installed elsewhere, use --openvdm-config so its vessel settings are "
            "offered.\n",
            file=sys.stderr,
        )
    from_openvdm = {}
    if openvdm_yaml is not None:
        block = load_openvdm_yaml(openvdm_yaml).get("vessel")
        from_openvdm = vessel_settings(block, "").settings
        if not block:
            # e.g. an openvdm.yaml carried over from OpenVDM 2.16
            print(
                f"{openvdm_yaml} has no vessel block (OpenVDM 2.17 adds one: see its "
                "openvdm.yaml.dist); the vessel fields' defaults are the site TOML's, or blank.\n",
                file=sys.stderr,
            )
    # Re-running over an existing file starts from its values
    existing = str(args.output) != STDOUT and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    if defaults.ignored:
        print(f"Dropping {', '.join(defaults.ignored)}: no longer used\n", file=sys.stderr)
    site = prompt_site_config(defaults, from_openvdm=from_openvdm)
    write_text(site_config_to_toml(site), args.output)
    return 0


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


def add_commands(commands) -> None:
    """`openvdm`, `config-from-openvdm` and `site-config`, on core's subparsers"""
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
    add_theme(hook)
    # OpenVDM shows the hook's failure message, so every error is one line
    hook.set_defaults(handler=_openvdm, every_error_one_line=True)

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
    add_cruise_toml_output(from_openvdm)
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
            "openvdm.yaml whose vessel settings (OpenVDM 2.17) are the defaults where the site "
            f"TOML has none (default {DEFAULT_OPENVDM_CONFIG}, when it exists)"
        ),
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)
