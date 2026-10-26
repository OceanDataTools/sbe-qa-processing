"""Servers that can't be reached and R2R's HTTP errors are a one-line error, and -v/--verbose
(issue 33)."""

import io
import logging
import socket
import tarfile
import urllib.error

import pytest

from sbe_qa_processing import maps
from sbe_qa_processing.cli import main
from sbe_qa_processing.integrations.openvdm import hook
from sbe_qa_processing.integrations.r2r import catalog, download
from sbe_qa_processing.network import NetworkError, reaching

NO_DNS = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))
R2R_UNREACHABLE = (
    "couldn't reach R2R (service.rvdata.us): nodename nor servname provided, or not known. "
    "Check the network connection and try again"
)


def unreachable(*args, **kwargs):
    raise NO_DNS


@pytest.fixture(autouse=True)
def package_log_level():
    """main() sets the package's log level for -v; put it back for the other tests"""
    logger = logging.getLogger("sbe_qa_processing")
    level = logger.level
    yield
    logger.setLevel(level)


@pytest.mark.parametrize(
    "error, detail",
    [
        (NO_DNS, "nodename nor servname provided, or not known"),
        (
            urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
            "Connection refused",
        ),
        (TimeoutError("timed out"), "timed out"),
        (ConnectionResetError(54, "Connection reset by peer"), "Connection reset by peer"),
    ],
)
def test_reaching_names_the_service_and_host(error, detail):
    with pytest.raises(NetworkError) as raised, reaching("R2R", "https://service.rvdata.us/api/x"):
        raise error
    assert str(raised.value) == (
        f"couldn't reach R2R (service.rvdata.us): {detail}. Check the network connection and "
        "try again"
    )
    assert raised.value.__cause__ is error


def test_reaching_passes_http_errors_and_other_errors_through():
    not_found = urllib.error.HTTPError("https://service.rvdata.us/x", 404, "Not Found", {}, None)
    with pytest.raises(urllib.error.HTTPError), reaching("R2R", "https://service.rvdata.us/x"):
        raise not_found
    with pytest.raises(FileNotFoundError), reaching("R2R", "https://service.rvdata.us/x"):
        raise FileNotFoundError("not a network problem")


def test_unreachable_r2r_is_one_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(catalog.urllib.request, "urlopen", unreachable)
    assert main(["r2r", "fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert err == f"sbe-qa-processing: {R2R_UNREACHABLE}\n"


@pytest.mark.parametrize("position", ["before", "after"])
def test_vv_adds_the_traceback(position, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(catalog.urllib.request, "urlopen", unreachable)
    command = ["r2r", "config-from-catalog", "SP2613", "169847", "-o", "-"]
    args = ["-vv", *command] if position == "before" else [*command, "-vv"]
    assert main(args) == 1
    err = capsys.readouterr().err
    assert err.startswith("Traceback (most recent call last):")
    assert "urllib.error.URLError" in err  # the cause, then the NetworkError
    assert err.endswith(f"sbe-qa-processing: {R2R_UNREACHABLE}\n")


def test_unreachable_openvdm_is_the_hooks_one_line_message(monkeypatch, capsys):
    monkeypatch.setattr(hook.urllib.request, "urlopen", unreachable)
    assert main(["openvdm", "--site-root", "http://openvdm.example/"]) == 1
    assert capsys.readouterr().err == (
        "sbe-qa-processing: NetworkError: couldn't reach OpenVDM (openvdm.example): nodename "
        "nor servname provided, or not known. Check the network connection and try again\n"
    )


def test_unreachable_natural_earth_is_one_line(monkeypatch, capsys):
    class Unreachable:
        def geometries(self):
            raise NO_DNS

    monkeypatch.setattr(maps, "_feature", lambda *args: Unreachable())
    assert main(["fetch-map-data"]) == 1
    assert (
        "couldn't reach Natural Earth (naturalearth.s3.amazonaws.com)" in capsys.readouterr().err
    )


def test_unexpected_errors_still_raise(monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(catalog.urllib.request, "urlopen", broken)
    with pytest.raises(KeyError):
        main(["r2r", "config-from-catalog", "SP2613", "169847", "-o", "-"])


def test_the_hook_reports_even_unexpected_errors_in_one_line(monkeypatch, capsys):
    """OpenVDM displays the hook's failure message, so a bug is one line there too"""

    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(hook.urllib.request, "urlopen", broken)
    assert main(["openvdm", "--site-root", "http://openvdm.example/"]) == 1
    assert capsys.readouterr().err == "sbe-qa-processing: KeyError: 'a bug'\n"


@pytest.mark.parametrize(
    "flags, level",
    [
        ([], logging.NOTSET),
        (["-v"], logging.INFO),
        (["-vv"], logging.DEBUG),
        (["-vvv"], logging.DEBUG),
    ],
)
def test_verbose_sets_the_log_level(flags, level, monkeypatch):
    monkeypatch.setattr("sbe_qa_processing.maps.prefetch", list)
    logging.getLogger("sbe_qa_processing").setLevel(logging.NOTSET)
    assert main([*flags, "fetch-map-data"]) == 0
    assert logging.getLogger("sbe_qa_processing").level == level


def http_error(url, code, reason):
    return urllib.error.HTTPError(url, code, reason, {}, None)


def test_r2r_http_errors_are_one_line(monkeypatch, capsys):
    def unavailable(url, *args, **kwargs):
        raise http_error(url, 503, "Service Unavailable")

    monkeypatch.setattr(catalog.urllib.request, "urlopen", unavailable)
    assert main(["r2r", "config-from-catalog", "SP2613", "169847", "-o", "-"]) == 1
    assert capsys.readouterr().err == (
        "sbe-qa-processing: R2R's catalog (service.rvdata.us) answered 503 Service Unavailable; "
        "try again later\n"
    )


class FakeOpener:
    """Serves the bag, then answers R2R's QA report URLs with `qa` (bytes or an HTTP status)"""

    def __init__(self, qa):
        self.qa = qa

    def open(self, url, timeout=None):
        if "/qa/" not in url:
            return FakeResponse(url, bag())
        if isinstance(self.qa, int):
            raise http_error(url, self.qa, {404: "Not Found", 503: "Service Unavailable"}[self.qa])
        return FakeResponse(url, self.qa)


class FakeResponse(io.BytesIO):
    def __init__(self, url, body):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


def bag() -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        info = tarfile.TarInfo("bagit.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"bag\n"))
    return archive.getvalue()


@pytest.fixture
def released(monkeypatch):
    record = {"fileset_id": 169847, "download_url": "https://service.rvdata.us/bag"}
    monkeypatch.setattr(download, "fetch_r2r_records", lambda *args: [record])


def test_r2r_qa_report_http_error_after_the_bag(released, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(download, "build_opener", lambda: FakeOpener(503))
    assert main(["r2r", "fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 1
    destination = tmp_path / "SP2613_169847_ctd"
    assert (destination / "bagit.txt").exists()
    assert capsys.readouterr().err == (
        f"sbe-qa-processing: 1 raw files are in {destination}, but R2R's QA report isn't: "
        "R2R's QA report (service.rvdata.us) answered 503 Service Unavailable; try again later. "
        "Run again with --force to fetch it\n"
    )


def test_r2r_qa_report_not_found_is_no_report(released, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(download, "build_opener", lambda: FakeOpener(404))
    assert main(["r2r", "fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 0
    assert capsys.readouterr().err == "R2R has no QA report for this fileset\n"


def test_r2r_qa_report_downloaded(released, tmp_path, monkeypatch):
    monkeypatch.setattr(download, "build_opener", lambda: FakeOpener(b"<?xml version='1.0'?>"))
    assert main(["r2r", "fetch-fileset", "SP2613", "169847", "-o", str(tmp_path)]) == 0
    assert (tmp_path / "SP2613_169847_ctd" / "r2r_qa.2.0.xml").exists()
