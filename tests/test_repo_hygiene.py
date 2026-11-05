"""Guards that keep private data out of this public repository and the dependency list honest.

They catch:
  * a secret-bearing file (.env, a cookies export, a private key) that .gitignore stops covering
  * anything shaped like an API key, a private key or a cookie line in a file that is not ignored
  * a requirements.txt that drifts from pyproject.toml, or minimum versions that slip back to
    releases with known security advisories
  * a base URL that would make the program open file:// or ftp:// addresses
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from clipper import llm
from clipper import trend_research as tr

ROOT = Path(__file__).resolve().parent.parent


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.fixture
def in_git_checkout():
    if shutil.which("git") is None or _git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        pytest.skip("not a git checkout")


# --- .gitignore keeps private files out -----------------------------------------------------------------


def _ignored(path):
    # --no-index: judge by the ignore rules alone, even for a file git already tracks
    return _git("check-ignore", "-q", "--no-index", path).returncode == 0


@pytest.mark.usefixtures("in_git_checkout")
@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        "cookies.txt",
        "www.youtube.com_cookies.txt",  # what the "Get cookies.txt LOCALLY" extension calls its export
        "youtube-cookies.txt",
        "cookies.json",
        "www.youtube.com_cookies.json",
        "cookies.sqlite",
        "session.cookies",
        "key.pem",
        "server.key",
        "cert.p12",
        "cert.pfx",
        "id_rsa",
        "id_ed25519",
        "out/clip.mp4",
        "models/face_landmarker.task",
    ],
)
def test_private_and_generated_files_are_git_ignored(name):
    assert _ignored(name), f"{name} would be picked up by `git add .`; add a rule for it to .gitignore"


@pytest.mark.usefixtures("in_git_checkout")
def test_the_env_template_is_not_ignored_because_it_is_meant_to_be_shared():
    assert not _ignored(".env.example")


# --- nothing secret-shaped in files that would be committed -------------------------------------------------------

SECRET_SHAPES = {
    "a private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "an sk- style API key": r"\bsk-[A-Za-z0-9_\-]{20,}",
    "an AWS access key id": r"\bAKIA[0-9A-Z]{16}\b",
    "an Alibaba access key id": r"\bLTAI[A-Za-z0-9]{12,}",
    "a GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})",
    "a Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "a Slack token": r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
    "a line from a Netscape cookies file": r"^[^\t#\n]+\t(?:TRUE|FALSE)\t[^\t]*\t(?:TRUE|FALSE)\t\d+\t[^\t]+\t",
    "a Google/YouTube login cookie with its value": (
        r"\b(?:SAPISID|APISID|HSID|SSID|LOGIN_INFO|__Secure-[13]PSID|__Secure-[13]PAPISID)\b\s*[=\t]\s*[A-Za-z0-9._%/\-]{10,}"
    ),
    "a web address with a password in it": r"https?://[^/\s:@]+:[^/\s:@]+@",
}
MAX_SCANNED_BYTES = 1_000_000  # secrets live in small text files; skip videos someone dropped in the folder


def _secret_findings(paths, root):
    """Files whose text contains something secret-shaped. The matched text is never reported."""
    found = []
    for path in paths:
        if not path.is_file() or path.stat().st_size > MAX_SCANNED_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:  # binary
            continue
        text = data.decode("utf-8", errors="ignore")
        for label, pattern in SECRET_SHAPES.items():
            if re.search(pattern, text, re.MULTILINE):
                found.append(f"{path.relative_to(root)}: contains {label}")
    return found


def _would_be_committed():
    listing = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return [ROOT / name for name in listing.split("\0") if name]


@pytest.mark.usefixtures("in_git_checkout")
def test_no_file_that_would_be_committed_contains_a_secret():
    files = _would_be_committed()
    assert len(files) > 20  # the listing worked
    assert _secret_findings(files, ROOT) == []


# Built from fragments so this file never contains a complete secret-shaped string itself.
SAMPLES = {
    "a private key": "-----BEGIN " + "RSA PRIVATE KEY" + "-----",
    "an sk- style API key": "key = sk-" + "a1" * 12,
    "an AWS access key id": "AKIA" + "ABCDEFGHIJKLMNOP",
    "an Alibaba access key id": "LTAI" + "abcdefghijkl",
    "a GitHub token": "ghp_" + "a" * 36,
    "a Google API key": "AIza" + "a" * 35,
    "a Slack token": "xoxb-" + "1" * 12,
    "a line from a Netscape cookies file": "\t".join([".youtube.com", "TRUE", "/", "TRUE", "1999999999", "SID", "value"]),
    "a Google/YouTube login cookie with its value": "SAPISID=" + "a" * 20,
    "a web address with a password in it": "https://user:" + "pw" + "@example.com/x",
}
HARMLESS = [
    "CLIPPER_API_KEY=",  # the empty slot in .env.example
    'api_key="sk-test-key"',  # made-up values the tests use  # pragma: allowlist secret
    '"sk-abcdefgh1234"',
    '"sk-supersecret1234"',
    "ds-key",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    'REQUIRED_COOKIES = ["SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO"]',
]


def test_every_secret_shape_is_really_detected():
    assert SAMPLES.keys() == SECRET_SHAPES.keys()  # a new pattern needs a sample here
    for label, sample in SAMPLES.items():
        assert re.search(SECRET_SHAPES[label], sample, re.MULTILINE), f"the {label} pattern no longer matches its sample"


@pytest.mark.parametrize("text", HARMLESS)
def test_the_made_up_values_used_in_tests_do_not_look_like_secrets(text):
    for label, pattern in SECRET_SHAPES.items():
        assert not re.search(pattern, text, re.MULTILINE), f"{text!r} would be reported as {label}"


def test_the_scan_reports_the_file_and_the_kind_but_never_the_value(tmp_path):
    (tmp_path / "notes.txt").write_text("my key: " + SAMPLES["an sk- style API key"], encoding="utf-8")
    (tmp_path / "fine.txt").write_text("nothing to see", encoding="utf-8")
    (tmp_path / "video.bin").write_bytes(b"\0" + SAMPLES["a GitHub token"].encode())  # binary: skipped
    findings = _secret_findings(sorted(tmp_path.iterdir()), tmp_path)
    assert findings == ["notes.txt: contains an sk- style API key"]


# --- the dependency list ----------------------------------------------------------------------------------------


def _declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [Requirement(dep) for dep in data["project"]["dependencies"]]


def _pins():
    pins = {}
    for number, raw in enumerate((ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.!+_-]+)", line)
        assert match, f"requirements.txt line {number} is not an exact pin (name==version): {raw!r}"
        name = canonicalize_name(match.group(1))
        assert name not in pins, f"{name} appears twice in requirements.txt"
        pins[name] = Version(match.group(2))
    return pins


def test_requirements_txt_exists_at_the_root_and_pins_every_line_exactly():
    assert len(_pins()) >= len(_declared())


def test_every_direct_dependency_is_pinned_inside_the_range_pyproject_allows():
    pins = _pins()
    for req in _declared():
        name = canonicalize_name(req.name)
        assert name in pins, f"{req.name} is a direct dependency but is not pinned in requirements.txt"
        assert req.specifier.contains(pins[name], prereleases=True), (
            f"requirements.txt pins {req.name}=={pins[name]}, outside what pyproject.toml allows ({req.specifier})"
        )


# Releases older than these have published security advisories (PyPI's advisory database, 2026-11-05).
SAFE_FROM = {
    "opencv-contrib-python": "4.8.1.78",  # bundled libwebp, CVE-2023-4863
    "pillow": "13.0.0",  # image-parsing advisories in every older release
    "python-dotenv": "1.2.2",  # CVE-2026-28684
    "certifi": "2026.10.18",  # still trusts a root CA whose intermediates have compromised keys
}


def _minimum(req):
    lows = [Version(s.version) for s in req.specifier if s.operator in (">=", "==", "~=", ">")]
    return max(lows) if lows else None


def test_minimum_versions_exclude_releases_with_known_security_advisories():
    minimums = {canonicalize_name(req.name): _minimum(req) for req in _declared()}
    for name, safe in SAFE_FROM.items():
        assert minimums.get(name) is not None, f"{name} needs a minimum version in pyproject.toml"
        assert minimums[name] >= Version(safe), f"{name}>={minimums[name]} allows releases with known advisories; use >={safe}"


def test_the_pinned_versions_are_not_older_than_the_safe_ones():
    pins = _pins()
    for name, safe in SAFE_FROM.items():
        assert pins[canonicalize_name(name)] >= Version(safe)


def test_the_minimum_python_is_stated_the_same_way_everywhere():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    match = re.fullmatch(r">=\s*3\.(\d+)", data["project"]["requires-python"])
    assert match, "requires-python should be a single '>=3.N' bound"
    minor = int(match.group(1))
    assert minor >= 11  # Pillow 13, the first release without known advisories, needs Python 3.11
    setup = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert f"(3, {minor})" in setup and f"{minor}+" in setup
    cli_source = (ROOT / "clipper" / "cli.py").read_text(encoding="utf-8")
    assert f"(3, {minor})" in cli_source and f"3.{minor}+ required" in cli_source
    assert f"Python 3.{minor} or newer" in (ROOT / "README.md").read_text(encoding="utf-8")


# --- web addresses and file names -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["file:///etc/passwd", "ftp://example.com/v1", "gopher://example.com", "", "dashscope-intl.aliyuncs.com/v1", "https://"],
)
def test_the_search_address_has_to_be_a_web_address(bad):
    with pytest.raises(llm.SearchUnavailable, match="http"):
        llm.native_search_url(bad)


@pytest.mark.parametrize(
    "good",
    ["https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "http://127.0.0.1:8080/compatible-mode/v1"],
)
def test_ordinary_web_addresses_still_work(good):
    host = re.match(r"(https?://[^/]+)", good).group(1)
    assert llm.native_search_url(good) == host + llm.NATIVE_PATH


@pytest.mark.parametrize(
    "niche,region,name",
    [
        ("reality TV drama", "the US", "trends-2f3ad6c23c93.json"),
        (None, None, "trends-65e022f3d64a.json"),
        ("x", None, "trends-ebf569ce98c1.json"),
    ],
)
def test_cache_file_names_did_not_change_when_the_hash_call_was_hardened(niche, region, name, tmp_path):
    # These names were recorded before `usedforsecurity=False` was added; changing them would orphan old caches.
    assert tr.cache_path(niche, region, cache_dir=tmp_path).name == name
