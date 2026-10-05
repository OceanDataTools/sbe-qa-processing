"""Runs the full QA on a fileset: reads and converts every cast, then the R2R tests and the
science checks.
"""

import importlib.metadata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sbe_qa_processing import __version__
from sbe_qa_processing.cast import CastData, read_cast
from sbe_qa_processing.config import CruiseConfig, load_config
from sbe_qa_processing.fileset import Fileset, load_fileset
from sbe_qa_processing.r2r import R2RResult, run_r2r_tests
from sbe_qa_processing.science import CheckResult, run_science_checks


@dataclass
class QAResult:
    config: CruiseConfig
    fileset: Fileset
    casts: list[CastData]
    r2r: R2RResult
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


def run_qa(config: CruiseConfig | Path | str, fileset: Fileset | Path | str) -> QAResult:
    started = datetime.now(UTC)
    config_path = None
    if not isinstance(config, CruiseConfig):
        config_path = Path(config)
        config = load_config(config_path)
    if not isinstance(fileset, Fileset):
        fileset = load_fileset(fileset, manifest_path=config.manifest_path)
    # Configuration-only entries aren't casts
    casts = [read_cast(c) for c in fileset.casts if c.raw is not None or c.header is not None]
    r2r = run_r2r_tests(fileset, casts, config)
    science = {
        c.name: run_science_checks(c, config.thresholds)
        for c in casts
        if c.converted and not c.cast.is_deck_test
    }
    return QAResult(
        config=config,
        fileset=fileset,
        casts=casts,
        r2r=r2r,
        science=science,
        started=started,
        finished=datetime.now(UTC),
        versions=_versions(),
        config_path=config_path,
    )
