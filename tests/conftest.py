import os
import tarfile
import warnings
from pathlib import Path

import pytest

from sbe_qa_processing import maps

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


@pytest.fixture(autouse=True, scope="session")
def no_map_data():
    """No test downloads Natural Earth, or depends on whether it's cached: the whole session
    runs without map data (maps without coastlines), set before any fixture and through the
    environment, so a notebook's kernel has none either
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(maps.NO_MAP_DATA, "1")
        yield


@pytest.fixture(autouse=True)
def map_data(request, monkeypatch):
    """A test marked `map_data` uses Natural Earth when it's already cached, never downloading"""
    if request.node.get_closest_marker("map_data"):
        if not maps.cached():
            pytest.skip("run: uv run sbe-qa-processing fetch-map-data")
        monkeypatch.delenv(maps.NO_MAP_DATA)
