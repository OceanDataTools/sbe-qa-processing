import os
import tarfile
import warnings
from pathlib import Path

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", "The seawater library is deprecated")

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"
# The R2R validation filesets, committed (tests/data/pack_fileset.py packs them) and unpacked
# into data/ when they aren't there, so the tests and CI need nothing from R2R
COMMITTED = Path(__file__).parent / "data"


def pytest_sessionstart(session):
    """Unpack each committed fileset that data/ doesn't have (a fetched copy stays)"""
    for archive in sorted(COMMITTED.glob("*_ctd.tar.xz")):
        if not (DATA / archive.name.removesuffix(".tar.xz") / "bagit.txt").exists():
            DATA.mkdir(exist_ok=True)
            with tarfile.open(archive) as tar:
                tar.extractall(DATA, filter="data")


def r2r_fileset(name: str) -> Path:
    """A downloaded R2R fileset, or skip with the command that fetches it"""
    path = DATA / name
    if not (path / "bagit.txt").exists():
        cruise, fileset, _ = name.split("_")
        pytest.skip(f"run: uv run sbe-qa-processing r2r fetch-fileset {cruise} {fileset}")
    return path
