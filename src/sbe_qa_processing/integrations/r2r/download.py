"""Downloads a released R2R CTD fileset (a BagIt bag) and R2R's QA report, for validation.

R2R's download URL redirects either to a .tar.gz of the bag (Globus) or to the bag's directory
at NCEI; both are handled. The bag lands in <destination>/ (bagit.txt, manifest-md5.txt,
data/...), with R2R's report beside it as r2r_qa.2.0.xml (or r2r_qa.xml for older filesets).
"""

import http.cookiejar
import io
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sbe_qa_processing.integrations.r2r.catalog import R2RError, fetch_r2r_records, http_error
from sbe_qa_processing.network import reaching

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


def fetch(opener, url: str, what: str = "R2R's fileset download") -> tuple[str, bytes]:
    """The final URL after redirects, and the body. An HTTP error is an R2RError naming `what`"""
    try:
        with reaching(what, url), opener.open(url, timeout=DOWNLOAD_TIMEOUT) as response:
            return response.geturl(), response.read()
    except urllib.error.HTTPError as error:
        raise http_error(error, what, url) from error


def _is_child(href: str) -> bool:
    """Whether a listing's link is a file or subdirectory directly under it: not a parent link
    (../), another site (http://...) or a name that would escape the destination
    """
    if ":" in href:  # a scheme: another site, mailto:, ...
        return False
    name = urllib.parse.unquote(href).removesuffix("/")
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\")


def list_directory(opener, url: str) -> tuple[list[str], list[str]]:
    """Files and subdirectories of an Apache-style directory index"""
    _, body = fetch(opener, url)
    links = re.findall(r'href="([^"?/][^"]*)"', body.decode("utf-8", "replace"))
    names = [link for link in links if _is_child(link)]
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

    try:
        report = download_qa_report(opener, cruise_id, fileset_id, destination)
    except (R2RError, OSError) as error:  # OSError: NetworkError
        # The raw files are in; say what's missing and how to fetch it
        raise R2RError(
            f"{count} raw files are in {destination}, but R2R's QA report isn't: {error}. "
            "Run again with --force to fetch it",
            getattr(error, "status", None),
        ) from error
    return Download(count, bag_url, report)


def download_qa_report(opener, cruise_id: str, fileset_id: int, destination: Path) -> Path | None:
    """R2R's QA report into destination, or None when R2R has none"""
    qa_base = f"{R2R}/data/cruise/{cruise_id}/fileset/{fileset_id}/qa/"
    for suffix in ("qa.2.0.xml", "qa.xml"):
        try:
            _, qa = fetch(opener, f"{qa_base}{cruise_id}_{fileset_id}_{suffix}", "R2R's QA report")
        except R2RError as error:
            if error.status == 404:
                continue
            raise
        # R2R answers missing files with 200 and an "ERROR: ..." body
        if qa.lstrip().startswith(b"<?xml"):
            report = destination / f"r2r_{suffix}"
            report.write_bytes(qa)
            return report
    return None
