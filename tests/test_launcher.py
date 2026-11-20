"""CLIPPER.command, run for real with bash, against a stand-in CLIPPER folder.

It was copied to the Desktop and failed with "bash: setup.sh: No such file or directory", because it looks for everything next to
itself. These tests pin what it does now: it works in place, through a shortcut, and as a stray copy; and when it cannot find the folder
it says what to do."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "CLIPPER.command"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


def make_folder(root: Path, *, with_environment: bool = True) -> Path:
    """The files the launcher recognises a CLIPPER folder by, a setup.sh that only reports where it ran, and a fake python that is
    'ready' for the import check and 'starts the app' by saying where it was started."""
    root.mkdir(parents=True)
    (root / "setup.sh").write_text('#!/bin/bash\necho "SETUP-RAN-IN $(pwd -P)"\n')
    (root / "pyproject.toml").write_text("")
    (root / "clipper").mkdir()
    (root / "clipper" / "__init__.py").write_text("")
    if with_environment:
        python = root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/bash\nif [ "$1" = "-c" ]; then exit 0; fi\necho "APP-STARTED-IN $(pwd -P)"\n')
        python.chmod(0o755)
    shutil.copy2(LAUNCHER, root / "CLIPPER.command")
    return root


def launch(script: Path, home: Path, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "CLIPPER_HOME": ""}
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script)], cwd=home, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60
    )


def started_in(result: subprocess.CompletedProcess) -> str | None:
    for line in result.stdout.splitlines():
        if line.startswith("APP-STARTED-IN "):
            return line.removeprefix("APP-STARTED-IN ")
    return None


@pytest.fixture
def home(tmp_path):
    (tmp_path / "home" / "Desktop").mkdir(parents=True)
    return tmp_path / "home"


def test_in_its_own_folder_it_starts_the_app_there(home):
    folder = make_folder(home / "CLIPPER")
    result = launch(folder / "CLIPPER.command", home)
    assert started_in(result) == os.path.realpath(folder), result.stdout
    assert "This file is a copy" not in result.stdout


def test_a_copy_on_the_desktop_finds_the_folder_instead_of_failing_in_setup(home):
    # exactly what was reported: the launcher copied to the Desktop, the real folder at ~/CLIPPER
    folder = make_folder(home / "CLIPPER")
    copy = home / "Desktop" / "CLIPPER.command"
    shutil.copy2(LAUNCHER, copy)
    result = launch(copy, home)
    assert started_in(result) == os.path.realpath(folder), result.stdout
    assert "This file is a copy" in result.stdout and "setup.sh: No such file" not in result.stdout + result.stderr


@pytest.mark.parametrize("where", ["Documents", "Desktop", "Downloads"])
def test_a_copy_looks_in_the_usual_places(home, where):
    folder = make_folder(home / where / "CLIPPER")
    copy = home / "elsewhere" / "CLIPPER.command"
    copy.parent.mkdir()
    shutil.copy2(LAUNCHER, copy)
    assert started_in(launch(copy, home)) == os.path.realpath(folder)


def test_a_folder_named_by_clipper_home_is_found_wherever_it_is(home, tmp_path):
    folder = make_folder(tmp_path / "somewhere" / "odd" / "place")
    copy = home / "Desktop" / "CLIPPER.command"
    shutil.copy2(LAUNCHER, copy)
    assert started_in(launch(copy, home, extra_env={"CLIPPER_HOME": str(folder)})) == os.path.realpath(folder)


def test_a_shortcut_to_the_real_file_works_even_for_a_folder_in_an_unusual_place(home, tmp_path):
    folder = make_folder(tmp_path / "a" / "b" / "CLIPPER")
    link = home / "Desktop" / "CLIPPER.command"
    link.symlink_to(folder / "CLIPPER.command")
    result = launch(link, home)
    assert started_in(result) == os.path.realpath(folder), result.stdout
    assert "This file is a copy" not in result.stdout  # it followed the link, it did not have to search


def test_a_relative_shortcut_works_too(home, tmp_path):
    folder = make_folder(tmp_path / "a" / "b" / "CLIPPER")
    link = home / "Desktop" / "CLIPPER.command"
    link.symlink_to(os.path.relpath(folder / "CLIPPER.command", link.parent))
    assert started_in(launch(link, home)) == os.path.realpath(folder)


def test_a_chain_of_shortcuts_works(home, tmp_path):
    folder = make_folder(tmp_path / "a" / "CLIPPER")
    first = tmp_path / "first.command"
    first.symlink_to(folder / "CLIPPER.command")
    second = home / "Desktop" / "CLIPPER.command"
    second.symlink_to(first)
    assert started_in(launch(second, home)) == os.path.realpath(folder)


def test_with_no_clipper_folder_anywhere_it_says_what_to_do_instead_of_a_setup_error(home):
    copy = home / "Desktop" / "CLIPPER.command"
    shutil.copy2(LAUNCHER, copy)
    result = launch(copy, home)
    assert result.returncode == 1
    assert "not inside the CLIPPER folder" in result.stdout and "Make Alias" in result.stdout
    assert "No such file" not in result.stdout + result.stderr and "SETUP-RAN" not in result.stdout


def test_a_folder_that_only_looks_similar_is_not_mistaken_for_clipper(home):
    # a ~/CLIPPER with no setup.sh (say, a folder of notes) must not be treated as the app
    (home / "CLIPPER").mkdir()
    (home / "CLIPPER" / "notes.txt").write_text("hello")
    copy = home / "Desktop" / "CLIPPER.command"
    shutil.copy2(LAUNCHER, copy)
    assert "not inside the CLIPPER folder" in launch(copy, home).stdout


def test_a_folder_not_yet_set_up_runs_setup_in_the_right_place(home):
    folder = make_folder(home / "CLIPPER", with_environment=False)
    copy = home / "Desktop" / "CLIPPER.command"
    shutil.copy2(LAUNCHER, copy)
    result = launch(copy, home)
    assert f"SETUP-RAN-IN {os.path.realpath(folder)}" in result.stdout  # setup.sh ran in the CLIPPER folder, not on the Desktop


def test_a_path_with_spaces_is_fine(home):
    folder = make_folder(home / "My Videos" / "CLIPPER copy")
    assert started_in(launch(folder / "CLIPPER.command", home)) == os.path.realpath(folder)
