"""The facts printed when a download dies with a cryptic file error, and the one repair."""

import os

import certifi
import pytest
from certifi import core

from clipper import diagnostics

# What the real run printed, as it reached the code.
REAL_ERROR = "Section download failed: Unable to handle request: 2 unexpected error(s)"


@pytest.mark.parametrize(
    "message",
    [
        REAL_ERROR,
        "Failed to download video section: Unable to handle request: 2 unexpected error(s)",
        "FileNotFoundError: [Errno 2] No such file or directory",
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
    ],
)
def test_download_setup_failures_are_recognised(message):
    assert diagnostics.looks_like_environment_error(message)


@pytest.mark.parametrize(
    "message",
    ["Failed to burn captions: ffmpeg exploded", "HTTP Error 403: Forbidden", "No subtitle available for language: EN", ""],
)
def test_other_failures_are_not_mistaken_for_them(message):
    assert not diagnostics.looks_like_environment_error(message)


def test_a_healthy_install_has_no_certifi_problem():
    assert diagnostics.certifi_problem() is None
    assert diagnostics.explain(REAL_ERROR) is None  # nothing specific to say when the file is fine


def test_a_missing_certificate_file_is_named(monkeypatch):
    monkeypatch.setattr(certifi, "where", lambda: "/nowhere/cacert.pem")
    assert diagnostics.certifi_problem() == "certifi's certificate file is missing: /nowhere/cacert.pem"
    hint = diagnostics.explain(REAL_ERROR)
    assert "/nowhere/cacert.pem" in hint and "pip install --force-reinstall certifi" in hint
    assert diagnostics.explain("Failed to burn captions") is None  # only for failures this could explain


def test_an_empty_certificate_file_is_a_problem_too(monkeypatch, tmp_path):
    empty = tmp_path / "cacert.pem"
    empty.write_bytes(b"")
    monkeypatch.setattr(certifi, "where", lambda: str(empty))
    assert "empty" in diagnostics.certifi_problem()


def test_a_remembered_location_that_went_stale_is_found_again(monkeypatch):
    real = certifi.where()
    monkeypatch.setattr(core, "_CACERT_PATH", "/gone/cacert.pem")  # what certifi remembers
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert diagnostics.certifi_problem() == "certifi's certificate file is missing: /gone/cacert.pem"

    assert diagnostics.repair_certifi() is True
    assert diagnostics.certifi_problem() is None
    assert certifi.where() == real
    assert os.environ["SSL_CERT_FILE"] == real


def test_repair_gives_up_when_the_file_really_is_gone(monkeypatch):
    monkeypatch.setattr(core, "_CACERT_PATH", "/gone/cacert.pem")
    monkeypatch.setattr(certifi, "__file__", "/gone/certifi/__init__.py")  # the package is not where it was
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert diagnostics.repair_certifi() is False
    assert "SSL_CERT_FILE" not in os.environ  # nothing was changed on the way to giving up


def test_repair_touches_nothing_but_the_remembered_path(monkeypatch):
    ctx = core._CACERT_CTX
    before = core.exit_cacert_ctx
    monkeypatch.setattr(core, "_CACERT_PATH", "/gone/cacert.pem")
    assert diagnostics.repair_certifi() is True
    assert core._CACERT_CTX is ctx and core.exit_cacert_ctx is before  # no second exit hook, no reset context


def test_the_diagnostics_name_every_file_the_download_needs(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("secret-cookie-value", encoding="utf-8")
    text = "\n".join(diagnostics.describe_environment(cookies_path=str(cookies), work_dir=str(tmp_path)))
    for expected in ("Diagnostics", "python ", "yt-dlp ", "certifi file:", "SSL_CERT_FILE:", "Python's own CA file:",
                     "working folder:", "cookies file:", "temp folder:", "free disk space"):
        assert expected in text, expected
    assert f"{cookies} -> exists" in text
    assert "secret-cookie-value" not in text  # paths and whether they exist, never contents


def test_a_missing_cookies_file_is_flagged(tmp_path):
    text = "\n".join(diagnostics.describe_environment(cookies_path=str(tmp_path / "nope.txt"), work_dir=str(tmp_path)))
    assert "nope.txt -> MISSING" in text


def test_a_deleted_working_folder_is_reported_not_crashed_on(tmp_path, monkeypatch):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    text = "\n".join(diagnostics.describe_environment(work_dir=str(tmp_path)))
    assert "no longer exists" in text
