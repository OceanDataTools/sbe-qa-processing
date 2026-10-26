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
from sbe_qa_processing.readable import Readable, counts, plural, timestamp
from sbe_qa_processing.science import FAIL, NA, PASS, WARN, CheckResult, run_science_checks


@dataclass
class QAResult(Readable):
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

    @property
    def verdict(self) -> tuple[str, str]:
        """The checks in a line, under the PDF's title: the worst status of those that could be
        made, and which didn't pass, e.g. "1 of 7 integrity checks warns: Position inside the
        expected area (3 of 4 casts pass). 2 of 4 casts warn in the science checks: Salinity
        agreement (CTD001, CTD002)." Info checks are left out and n/a integrity
        checks named, never counted as problems. It's not a rating, which is R2R's
        """
        return verdict(self)

    def __str__(self) -> str:
        converted = sum(c.converted for c in self.casts)
        deck_tests = sum(c.cast.is_deck_test for c in self.casts)
        seconds = (self.finished - self.started).total_seconds()
        versions = ", ".join(f"{name} {version}" for name, version in self.versions.items())
        science = [check.status for checks in self.science.values() for check in checks]
        lines = [
            (
                f"{self.config.identifier}: {plural(len(self.casts), 'cast')} ({converted} "
                f"converted, {plural(deck_tests, 'deck test')}) in {self.fileset.root}"
            ),
            f"  run {timestamp(self.started)}, {seconds:.1f} s"
            + (f"; {versions}" if versions else ""),
            f"  integrity checks: {counts([c.status for c in self.integrity.fileset])}",
            *(f"    {problem}" for problem in _integrity_problems(self)),
            f"  science checks: {counts(science)}",
            *(f"    {problem}" for problem in _science_problems(self)),
        ]
        return "\n".join(lines)


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


def summary(result: QAResult) -> list[str]:
    """Indented lines on the integrity and science checks that warned or failed"""
    integrity, science = _integrity_problems(result), _science_problems(result)
    return [
        f"  integrity checks: {len(integrity)} warnings/failures",
        *(f"    {problem}" for problem in integrity),
        f"  science checks: {len(science)} warnings/failures",
        *(f"    {problem}" for problem in science),
    ]


def _integrity_problems(result: QAResult) -> list[str]:
    """The fileset's integrity checks that warned or failed"""
    return [
        f"{check.name} {check.status} ({check.value})"
        + (f": {check.detail}" if check.detail else "")
        for check in result.integrity.fileset
        if check.status in ("warn", "fail")
    ]


def _science_problems(result: QAResult) -> list[str]:
    """Each cast's science checks that warned or failed"""
    return [
        f"{cast}: {check.name} {check.status} ({check.value})"
        for cast, checks in result.science.items()
        for check in checks
        if check.status in ("warn", "fail")
    ]


VERDICT_CASTS = 3  # at most this many casts are named for a science check; more are counted


def _worst(statuses) -> str:
    statuses = set(statuses)
    return FAIL if FAIL in statuses else WARN if WARN in statuses else PASS


def _which(names: list[str], total: int) -> str:
    """The casts a check didn't pass in: all of them, their names, or how many"""
    if len(names) == total > 1:
        return f"all {total}"
    return ", ".join(names) if len(names) <= VERDICT_CASTS else f"{len(names)} casts"


def verdict(result: QAResult) -> tuple[str, str]:
    """QAResult.verdict: (status, text)"""
    sentences, statuses = [], []

    checks = result.integrity.fileset
    judged = [c for c in checks if c.status in (PASS, WARN, FAIL)]
    missing = [c.name for c in checks if c.status == NA]
    problems = [c for c in judged if c.status != PASS]
    total = len(judged) + len(missing)
    if not judged:
        sentences.append("No integrity check could be made.")
    elif not problems:
        statuses.append(PASS)
        if missing:
            verb = "pass" if len(judged) > 1 else "passes"
            sentences.append(f"{len(judged)} of {plural(total, 'integrity check')} {verb}.")
        elif total == 1:
            sentences.append("The integrity check passes.")
        else:
            sentences.append(f"All {total} integrity checks pass.")
    else:
        status = _worst(c.status for c in problems)
        statuses.append(status)
        verb = status if len(problems) > 1 else f"{status}s"
        listed = "; ".join(f"{c.name} ({c.value})" for c in problems)
        sentences.append(
            f"{len(problems)} of {plural(total, 'integrity check')} {verb}: {listed}."
        )

    casts = {
        cast: [c for c in cs if c.status in (PASS, WARN, FAIL)]
        for cast, cs in result.science.items()
    }
    casts = {cast: cs for cast, cs in casts.items() if cs}
    bad = {cast: cs for cast, cs in casts.items() if any(c.status != PASS for c in cs)}
    if not casts:
        sentences.append("No cast for the science checks.")
    elif not bad:
        statuses.append(PASS)
        every = "The cast passes" if len(casts) == 1 else f"All {len(casts)} casts pass"
        sentences.append(f"{every} the science checks.")
    else:
        status = _worst(c.status for cs in bad.values() for c in cs)
        statuses.append(status)
        # By check, with the casts it didn't pass in: named when few, else counted
        by_check: dict[str, list[str]] = {}
        for cast, cs in bad.items():
            for c in cs:
                if c.status != PASS:
                    by_check.setdefault(c.name, []).append(cast)
        listed = ", ".join(
            f"{name} ({_which(names, len(casts))})" for name, names in by_check.items()
        )
        verb = status if len(bad) > 1 else f"{status}s"
        sentences.append(
            f"{len(bad)} of {plural(len(casts), 'cast')} {verb} in the science checks: {listed}."
        )
    if missing:
        sentences.append(f"n/a: {', '.join(missing)}.")
    return (_worst(statuses) if statuses else NA), " ".join(sentences)
