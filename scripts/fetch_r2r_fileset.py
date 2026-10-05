"""Downloads a released R2R CTD fileset (a BagIt bag) and R2R's QA report.

Usage: python scripts/fetch_r2r_fileset.py CRUISE_ID FILESET_ID [--out data]

Creates <out>/<CRUISE_ID>_<FILESET_ID>_ctd/ containing the bag (bagit.txt, manifest-md5.txt,
data/...) and r2r_qa.2.0.xml. R2R's download URL redirects either to a .tar.gz of the bag
(Globus) or to the bag's directory at NCEI; both are handled. Uses only the standard library.
"""

import argparse
import http.cookiejar
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

R2R = "https://service.rvdata.us"


def build_opener() -> urllib.request.OpenerDirector:
    # NCEI redirects in a loop unless its session cookie is kept
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def fetch(opener, url: str) -> tuple[str, bytes]:
    with opener.open(url, timeout=120) as response:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cruise_id")
    parser.add_argument("fileset_id", type=int)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    opener = build_opener()
    _, body = fetch(opener, f"{R2R}/api/fileset/?cruise_id={urllib.parse.quote(args.cruise_id)}")
    filesets = [f for f in json.loads(body)["data"] if f["fileset_id"] == args.fileset_id]
    if not filesets or not filesets[0].get("download_url"):
        print(f"{args.cruise_id} fileset {args.fileset_id} isn't released for download")
        return 1

    destination = args.out / f"{args.cruise_id}_{args.fileset_id}_ctd"
    bag_url, body = fetch(opener, filesets[0]["download_url"])
    if body.lstrip().startswith(b"ERROR"):
        # R2R lists a download URL for some filesets that aren't actually online
        print(f"{bag_url}: {body.decode(errors='replace').strip()}")
        return 1
    # R2R and Globus serve a gzipped tar of the bag (not always with a .tar.gz URL)
    if body[:2] == b"\x1f\x8b":
        count = extract_bag(body, destination)
    else:
        bag_url = bag_url if bag_url.endswith("/") else bag_url + "/"
        count = download_tree(opener, bag_url, destination)

    qa_base = f"{R2R}/data/cruise/{args.cruise_id}/fileset/{args.fileset_id}/qa/"
    for name in (
        f"{args.cruise_id}_{args.fileset_id}_qa.2.0.xml",
        f"{args.cruise_id}_{args.fileset_id}_qa.xml",
    ):
        try:
            _, qa = fetch(opener, qa_base + name)
        except OSError:
            continue
        # R2R answers missing files with 200 and an "ERROR: ..." body
        if qa.lstrip().startswith(b"<?xml"):
            (
                destination / name.replace(f"{args.cruise_id}_{args.fileset_id}_", "r2r_")
            ).write_bytes(qa)
            break
    else:
        print("no R2R QA report found")

    print(f"{count} files from {bag_url} -> {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
