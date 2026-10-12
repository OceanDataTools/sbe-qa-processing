"""Runs the core QA on a fileset: reads and converts every cast, then the integrity and science
checks.
"""

import importlib.metadata
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing import __version__
from sbe_qa_processing.cast import CastData, read_cast
from sbe_qa_processing.config import QAConfig, load_config
from sbe_qa_processing.fileset import Fileset, load_fileset
from sbe_qa_processing.integrity import IntegrityResult, run_integrity_checks
from sbe_qa_processing.science import CheckResult, run_science_checks


@dataclass
class QAResult:
    config: QAConfig
    fileset: Fileset
    casts: list[CastData]
    integrity: IntegrityResult
    science: dict[str, list[CheckResult]]
    started: datetime
    finished: datetime
    versions: dict[str, str] = field(default_factory=dict)
    config_path: Path | None = None  # set when the config was loaded from a file

    @property
    def science_casts(self) -> list[CastData]:
        """Converted casts that aren't deck tests"""
        return [c for c in self.casts if c.converted and not c.cast.is_deck_test]


def _versions() -> dict[str, str]:
    versions = {"sbe-qa-processing": __version__}
    for package in ("seabirdscientific", "gsw", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unknown"
    return versions


def resolve_config(config: QAConfig | Path | str | None, fileset_dir: Path | str) -> QAConfig:
    """The config for a run: as given, loaded from a TOML, or empty, titled after the fileset
    directory when it has no cruise ID
    """
    if config is None:
        config = QAConfig()
    elif not isinstance(config, QAConfig):
        config = load_config(config)
    if not config.cruise_id:
        config = replace(config, cruise_id=Path(fileset_dir).resolve().name)
    return config


def run_qa(fileset: Fileset | Path | str, config: QAConfig | Path | str | None = None) -> QAResult:
    started = datetime.now(UTC)
    config_path = None if config is None or isinstance(config, QAConfig) else Path(config)
    root = fileset.root if isinstance(fileset, Fileset) else fileset
    config = resolve_config(config, root)
    if not isinstance(fileset, Fileset):
        fileset = load_fileset(fileset, manifest_path=config.manifest_path)
    # Configuration-only entries aren't casts
    casts = [read_cast(c) for c in fileset.casts if c.raw is not None or c.header is not None]
    integrity = run_integrity_checks(fileset, casts, config)
    science = {
        c.name: run_science_checks(c, config.thresholds)
        for c in casts
        if c.converted and not c.cast.is_deck_test
    }
    return QAResult(
        config=config,
        fileset=fileset,
        casts=casts,
        integrity=integrity,
        science=science,
        started=started,
        finished=datetime.now(UTC),
        versions=_versions(),
        config_path=config_path,
    )
