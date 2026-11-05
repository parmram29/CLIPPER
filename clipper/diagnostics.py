"""What to look at when a download dies with a cryptic file error, and a repair for one known cause.

Not part of upstream. Prompted by a real run: the first clip of three rendered, then the second clip's
download died with ``FileNotFoundError: [Errno 2] No such file or directory`` from BOTH of yt-dlp's HTTP
handlers (``Unable to handle request: 2 unexpected error(s)``), before any network traffic. Reproducing
that exact message in isolation shows it is what yt-dlp raises when the certificate file it loads
(``certifi.where()``) is not there. Why it would vanish mid-run is not known yet, so this module
reports the facts that tell the causes apart instead of guessing.
"""

from __future__ import annotations

import os
import platform
import shutil
import ssl
import sys
import tempfile

# Phrases that mean "the download machinery itself broke", as opposed to a bad video or an ffmpeg error.
ENVIRONMENT_ERROR_MARKERS = ("Unable to handle request", "FileNotFoundError", "[Errno 2]", "CERTIFICATE_VERIFY_FAILED")


def looks_like_environment_error(message: str) -> bool:
    """True for failures of the download setup (certificates, files it needs), worth one more try."""
    return any(marker in message for marker in ENVIRONMENT_ERROR_MARKERS)


def certifi_problem() -> str | None:
    """None when certifi's certificate file can be used, otherwise a one-line reason."""
    try:
        import certifi
    except ImportError:
        return "certifi is not installed"
    try:
        path = certifi.where()
    except Exception as exc:  # the lookup itself can fail, e.g. a broken install
        return f"certifi could not locate its certificate file ({type(exc).__name__}: {exc})"
    if not os.path.isfile(path):
        return f"certifi's certificate file is missing: {path}"
    if os.path.getsize(path) == 0:
        return f"certifi's certificate file is empty: {path}"
    return None


def repair_certifi() -> bool:
    """Point certifi, and SSL_CERT_FILE, at the certificate file inside the installed package.

    True if certifi is usable afterwards. certifi works out where its file is once and remembers the
    answer for the life of the process, so a remembered path that has since gone stale stays wrong until
    it is replaced. Only that remembered path is replaced; the rest of certifi's state is left alone.
    """
    try:
        import certifi
        from certifi import core
    except ImportError:
        return False
    path = os.path.join(os.path.dirname(certifi.__file__), "cacert.pem")
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    core._CACERT_PATH = path
    os.environ["SSL_CERT_FILE"] = path
    return certifi_problem() is None


def _exists(path: str | None) -> str:
    if not path:
        return "(not set)"
    return "exists" if os.path.exists(path) else "MISSING"


def describe_environment(cookies_path: str | None = None, work_dir: str | None = None) -> list[str]:
    """Plain-text facts about the machine and the files a download needs. Never includes secrets."""
    lines = ["Diagnostics (include these lines if you ask for help):"]
    lines.append(f"  python {sys.version.split()[0]} | {platform.platform()} | {platform.machine()}")

    try:
        import yt_dlp

        version = yt_dlp.version.__version__
    except Exception as exc:
        version = f"not importable ({type(exc).__name__})"
    try:
        import requests  # noqa: F401

        has_requests = "yes"
    except Exception:
        has_requests = "no"
    lines.append(f"  yt-dlp {version} | requests installed: {has_requests}")

    problem = certifi_problem()
    try:
        import certifi

        where = certifi.where()
    except Exception:
        where = None
    lines.append(f"  certifi file: {where} -> {_exists(where)}" + (f"  <-- {problem}" if problem else ""))

    cert_env = os.environ.get("SSL_CERT_FILE")
    lines.append(f"  SSL_CERT_FILE: {cert_env or '(not set)'} -> {_exists(cert_env)}")
    paths = ssl.get_default_verify_paths()
    lines.append(f"  Python's own CA file: {paths.cafile or '(none)'} -> {_exists(paths.cafile)}")

    try:
        cwd = os.getcwd()
    except OSError:
        cwd = "(the folder this was started from no longer exists)"
    lines.append(f"  working folder: {cwd}")

    cookies = cookies_path or os.environ.get("CLIPPER_COOKIES")
    lines.append(f"  cookies file: {cookies or '(not set)'} -> {_exists(cookies)}")
    temp = tempfile.gettempdir()
    lines.append(f"  temp folder: {temp} -> {_exists(temp)}")

    target = work_dir or "."
    try:
        free_gb = shutil.disk_usage(target).free / 1e9
        lines.append(f"  free disk space where clips are written: {free_gb:.1f} GB")
    except OSError:
        lines.append("  free disk space: could not be read")
    return lines


def explain(message: str) -> str | None:
    """A plain-English hint for a failure message, or None when there is nothing specific to say."""
    problem = certifi_problem()
    if problem and looks_like_environment_error(message):
        return f"{problem}. Reinstall it with: pip install --force-reinstall certifi"
    return None
