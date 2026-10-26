"""Packs a fetched R2R validation fileset into tests/data/<name>.tar.xz, which the tests unpack
into data/ when it isn't there (tests/conftest.py).

    uv run python tests/data/pack_fileset.py data/SP2613_169847_ctd

Files are added in name order with their owners and times cleared, so packing the same fileset
again gives the same archive.
"""

import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).parent


def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def pack(fileset: Path) -> Path:
    archive = HERE / f"{fileset.name}.tar.xz"
    with tarfile.open(archive, "w:xz", format=tarfile.PAX_FORMAT) as tar:
        for path in [fileset, *sorted(fileset.rglob("*"))]:
            name = path.relative_to(fileset.parent).as_posix()
            tar.add(path, name, recursive=False, filter=normalized)
    return archive


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(pack(Path(argument)))
