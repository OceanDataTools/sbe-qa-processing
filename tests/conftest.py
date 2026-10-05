import os
import warnings
from pathlib import Path

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", "The seawater library is deprecated")

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"


def r2r_fileset(name: str) -> Path:
    """A downloaded R2R fileset, or skip with the command that fetches it"""
    path = DATA / name
    if not (path / "bagit.txt").exists():
        cruise, fileset, _ = name.split("_")
        pytest.skip(f"run: python scripts/fetch_r2r_fileset.py {cruise} {fileset}")
    return path
