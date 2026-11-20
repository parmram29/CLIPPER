import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from clipper import cli

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "clipper" / "gui" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
LAUNCHER = ROOT / "CLIPPER.command"


def test_the_page_loads_only_its_own_files_and_nothing_from_the_internet():
    for url in re.findall(r'(?:src|href)="([^"#][^"]*)"', HTML):
        assert url.startswith("/static/"), url
    assert "@import" not in CSS and not re.search(r"url\(\s*['\"]?(https?:)?//", CSS)
    assert not re.search(r"""(?:fetch|open|import|importScripts)\(\s*['"`]https?:""", JS)
    assert "<script>" not in HTML and not re.search(r"<script[^>]*>[^<]+</script>", HTML)  # no inline script


def test_the_script_never_builds_html_from_text_or_runs_text_as_code():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setInterval(\"", "setTimeout(\""):
        assert banned not in JS, banned


def test_the_page_never_needs_an_inline_style_which_the_security_policy_forbids():
    assert not re.search(r'style: "[^"]*:[^"]*"', JS)  # e.g. style: "margin-top:16px"
    assert " style=" not in HTML and "<style" not in HTML


def test_every_class_the_script_uses_has_a_style():
    used = set()
    for text in re.findall(r'class: "([^"]+)"', JS):
        used |= {name for name in text.split() if name}
    missing = sorted(name for name in used if not re.search(rf"\.{re.escape(name)}(?![\w-])", CSS))
    assert missing == [], f"classes used in app.js with no rule in style.css: {missing}"


@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed here; the script is also checked in a real browser by hand")
def test_the_script_is_valid_javascript():
    done = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_page_has_the_basics():
    assert '<html lang="en">' in HTML and 'name="viewport"' in HTML and "<title>CLIPPER</title>" in HTML
    assert 'id="view"' in HTML and 'id="toast"' in HTML and 'role="status"' in HTML
    assert (STATIC / "icon.svg").read_text(encoding="utf-8").startswith("<svg")


def test_the_page_files_ship_inside_the_package():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "clipper.gui" in config["tool"]["setuptools"]["packages"]
    assert config["tool"]["setuptools"]["package-data"]["clipper.gui"] == ["static/*"]
    assert {p.name for p in STATIC.iterdir()} == {"app.js", "style.css", "index.html", "icon.svg"}


def test_the_gui_command_exists_and_takes_no_way_to_expose_it_to_the_network():
    parser = cli.build_parser()
    args = parser.parse_args(["gui", "--port", "9000", "--no-browser"])
    assert (args.command, args.port, args.no_browser) == ("gui", 9000, True)
    assert not hasattr(args, "host")  # there is deliberately no option to listen on anything but this computer


# --- the double-click launcher --------------------------------------------------------------------------------------


def test_the_launcher_is_an_executable_shell_script_with_valid_syntax():
    assert LAUNCHER.is_file() and os.access(LAUNCHER, os.X_OK)
    text = LAUNCHER.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert subprocess.run(["bash", "-n", str(LAUNCHER)], capture_output=True).returncode == 0


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_git_records_the_launcher_as_executable_so_a_fresh_checkout_can_be_double_clicked():
    listed = subprocess.run(["git", "ls-files", "-s", "CLIPPER.command"], cwd=ROOT, capture_output=True, text=True).stdout
    assert listed.startswith("100755 "), listed or "CLIPPER.command is not tracked by git yet"


def test_the_launcher_starts_the_app_from_its_own_folder_and_repairs_a_moved_environment():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'dirname "$SOURCE"' in text and 'cd "$HERE"' in text  # works wherever the folder was moved to (tests/test_launcher.py runs it)
    assert '"$PY" -m clipper gui' in text  # not the .venv/bin/clipper launcher, whose path breaks when a folder is moved
    assert "clipper.gui.server" in text and "bash setup.sh" in text
    for risky in ("sudo", "rm -rf", "curl", "wget", "| sh", "| bash", "eval "):
        assert risky not in text, risky
