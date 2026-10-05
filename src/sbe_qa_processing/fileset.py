"""Finds the casts in an SBE 9/911plus fileset: a BagIt bag (as R2R and NCEI distribute them)
or a plain directory of Seasave output.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

RAW_DATA_SUFFIXES = (".hex", ".dat")
CONFIG_SUFFIXES = (".xmlcon", ".con")
HEADER_SUFFIX = ".hdr"
BOTTLE_SUFFIX = ".bl"
# R2R lists casts whose file name mentions a dock/deck test separately, and leaves them out of
# the navigation tests
DECK_TEST_PATTERN = re.compile(r"deck|dock|test", re.IGNORECASE)


@dataclass
class Cast:
    name: str
    raw: Path | None = None
    config: Path | None = None
    header: Path | None = None
    bottles: Path | None = None

    @property
    def is_deck_test(self) -> bool:
        return bool(DECK_TEST_PATTERN.search(self.name))

    @property
    def has_all_raw_files(self) -> bool:
        return self.raw is not None and self.config is not None and self.header is not None


@dataclass
class ManifestEntry:
    path: str  # relative to the bag root, e.g. data/CAST1.hex
    md5: str
    file_id: str = ""
    size: int | None = None


@dataclass
class Fileset:
    root: Path
    data_dir: Path
    casts: list[Cast]
    manifest: list[ManifestEntry] = field(default_factory=list)
    is_bag: bool = False
    # when the manifest was written; files modified later aren't in it yet
    manifest_mtime: float | None = None

    @property
    def raw_casts(self) -> list[Cast]:
        """Casts with a raw data file (.hex/.dat), the unit R2R counts"""
        return [cast for cast in self.casts if cast.raw is not None]

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest(root: Path) -> list[ManifestEntry]:
    """manifest-md5.txt (BagIt), enriched with R2R file ids and sizes from file-info.txt"""
    manifest_path = root / "manifest-md5.txt"
    if not manifest_path.exists():
        return []
    entries = {}
    for line in manifest_path.read_text().splitlines():
        if line.strip():
            md5, path = line.split(maxsplit=1)
            entries[path.strip()] = ManifestEntry(path=path.strip(), md5=md5.lower())
    info_path = root / "file-info.txt"
    if info_path.exists():
        for line in info_path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and parts[2] in entries:
                entries[parts[2]].file_id = parts[0]
                entries[parts[2]].size = int(parts[3]) if parts[3].isdigit() else None
    return sorted(entries.values(), key=lambda e: e.path.lower())


_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")


def _read_external_manifest(manifest_path: Path, root: Path) -> list[ManifestEntry]:
    """ "<md5> <path>" lines (e.g. OpenVDM's MD5 summary, paths relative to the manifest's
    directory) for the files under root, as paths relative to root. Entries without a real hash
    (OpenVDM writes asterisks for files over its size limit) are skipped
    """
    base = manifest_path.parent.resolve()
    root = root.resolve()
    entries = []
    for line in manifest_path.read_text(errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or not _MD5.match(parts[0]):
            continue
        full = (base / parts[1]).resolve()
        if full.is_relative_to(root):
            entries.append(
                ManifestEntry(path=full.relative_to(root).as_posix(), md5=parts[0].lower())
            )
    return sorted(entries, key=lambda e: e.path.lower())


def load_fileset(path: Path | str, manifest_path: Path | str | None = None) -> Fileset:
    """Casts in a bag or directory. manifest_path supplies checksums for a plain directory"""
    path = Path(path)
    is_bag = (path / "bagit.txt").exists()
    data_dir = path / "data" if is_bag else path
    if not data_dir.is_dir():
        raise FileNotFoundError(f"no data directory at {data_dir}")

    casts: dict[str, Cast] = {}
    for file in sorted(data_dir.rglob("*")):
        if not file.is_file():
            continue
        suffix = file.suffix.lower()
        # Casts are matched by name regardless of case, e.g. CTD1.hex with CTD1.XMLCON
        key = file.relative_to(data_dir).with_suffix("").as_posix().lower()
        if suffix in RAW_DATA_SUFFIXES:
            attribute = "raw"
        elif suffix in CONFIG_SUFFIXES:
            attribute = "config"
        elif suffix == HEADER_SUFFIX:
            attribute = "header"
        elif suffix == BOTTLE_SUFFIX:
            attribute = "bottles"
        else:
            continue
        cast = casts.setdefault(key, Cast(name=file.stem))
        if attribute == "raw":
            cast.name = file.stem  # prefer the raw file's spelling of the name
        setattr(cast, attribute, file)

    manifest, manifest_mtime = [], None
    if is_bag:
        # A bag is static, so its manifest is never stale (and copying a bag resets file times)
        manifest = _read_manifest(path)
    elif manifest_path is not None and Path(manifest_path).exists():
        manifest = _read_external_manifest(Path(manifest_path), path)
        manifest_mtime = Path(manifest_path).stat().st_mtime
    return Fileset(
        root=path,
        data_dir=data_dir,
        casts=sorted(casts.values(), key=lambda c: c.name.lower()),
        manifest=manifest,
        is_bag=is_bag,
        manifest_mtime=manifest_mtime,
    )
