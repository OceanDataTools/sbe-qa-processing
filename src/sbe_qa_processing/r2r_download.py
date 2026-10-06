"""Downloads a released R2R CTD fileset (a BagIt bag) and R2R's QA report, for validation.

R2R's download URL redirects either to a .tar.gz of the bag (Globus) or to the bag's directory
at NCEI; both are handled. The bag lands in <destination>/ (bagit.txt, manifest-md5.txt,
data/...), with R2R's report beside it as r2r_qa.2.0.xml (or r2r_qa.xml for older filesets).
"""

import http.cookiejar
import io
import re
import tarfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sbe_qa_processing.config import R2RError, fetch_r2r_records

R2R = "https://service.rvdata.us"
DOWNLOAD_TIMEOUT = 120  # [s]


@dataclass
class Download:
    files: int
    bag_url: str
    qa_report: Path | None  # None when R2R has no QA report for the fileset


def build_opener() -> urllib.request.OpenerDirector:
    # NCEI redirects in a loop unless its session cookie is kept
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def fetch(opener, url: str) -> tuple[str, bytes]:
    """The final URL after redirects, and the body"""
    with opener.open(url, timeout=DOWNLOAD_TIMEOUT) as response:
        return response.geturl(), response.read()


def list_directory(opener, url: str) -> tuple[list[str], list[str]]:
    """Files and subdirectories of an Apache-style directory index"""
    _, body = fetch(opener, url)
    names = re.findall(r'href="([^"?/][^"]*)"', body.decode("utf-8", "replace"))
    files = [n for n in names if not n.endswith("/")]
    directories = [n for n in names if n.endswith("/")]
    return files, directories


def download_tree(opener, url: str, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    files, directories = list_directory(opener, url)
    count = 0
    for name in files:
        _, body = fetch(opener, urllib.parse.urljoin(url, name))
        (destination / urllib.parse.unquote(name)).write_bytes(body)
        count += 1
    for name in directories:
        count += download_tree(
            opener, urllib.parse.urljoin(url, name), destination / urllib.parse.unquote(name)
        )
    return count


def extract_bag(archive: bytes, destination: Path) -> int:
    """Extracts the bag in a .tar.gz into destination, so destination/bagit.txt exists.
    The bag can be nested at any depth in the archive (e.g. CRUISE/FILESET/bagit.txt)
    """
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        roots = [Path(m.name).parent for m in members if Path(m.name).name == "bagit.txt"]
        if not roots:
            raise ValueError("archive contains no bagit.txt")
        root = min(roots, key=lambda r: len(r.parts))
        for member in members:
            path = Path(member.name)
            if not member.isfile() or not path.is_relative_to(root):
                continue
            member.name = str(path.relative_to(root))
            # the "data" filter rejects absolute paths, links and anything outside destination
            tar.extract(member, destination, filter="data")
            count += 1
    return count


def download_fileset(cruise_id: str, fileset_id: int, destination: Path) -> Download:
    """The fileset's bag and R2R's QA report, into destination"""
    filesets = [
        f for f in fetch_r2r_records("fileset", cruise_id) if f.get("fileset_id") == fileset_id
    ]
    if not filesets or not filesets[0].get("download_url"):
        raise R2RError(f"{cruise_id} fileset {fileset_id} isn't released for download")

    opener = build_opener()
    bag_url, body = fetch(opener, filesets[0]["download_url"])
    if body.lstrip().startswith(b"ERROR"):
        # R2R lists a download URL for some filesets that aren't actually online
        raise R2RError(f"{bag_url}: {body.decode(errors='replace').strip()}")
    # R2R and Globus serve a gzipped tar of the bag (not always with a .tar.gz URL)
    if body[:2] == b"\x1f\x8b":
        count = extract_bag(body, destination)
    else:
        bag_url = bag_url if bag_url.endswith("/") else bag_url + "/"
        count = download_tree(opener, bag_url, destination)

    qa_base = f"{R2R}/data/cruise/{cruise_id}/fileset/{fileset_id}/qa/"
    for suffix in ("qa.2.0.xml", "qa.xml"):
        try:
            _, qa = fetch(opener, f"{qa_base}{cruise_id}_{fileset_id}_{suffix}")
        except OSError:
            continue
        # R2R answers missing files with 200 and an "ERROR: ..." body
        if qa.lstrip().startswith(b"<?xml"):
            report = destination / f"r2r_{suffix}"
            report.write_bytes(qa)
            return Download(count, bag_url, report)
    return Download(count, bag_url, None)
