"""MediaPipe is optional: it has no build for every Python/CPU (for example, Intel
Macs on Python 3.13+), and the install used to fail outright because of it."""

import json
import subprocess
import sys
import types

import pytest

from clipper import cli

from conftest import highlight


@pytest.mark.parametrize(
    "mode,available,expected",
    [
        ("auto", True, "face"),
        ("auto", False, "centered"),
        ("face", True, "face"),
        ("centered", False, "centered"),
        ("centered", True, "centered"),
        ("original", False, "original"),
    ],
)
def test_resolve_reframe(mode, available, expected):
    assert cli.resolve_reframe(mode, available) == expected


def test_explicit_face_without_mediapipe_is_an_error_with_the_fix():
    with pytest.raises(RuntimeError, match=r"clipper\[face\]"):
        cli.resolve_reframe("face", available=False)


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "sessions" / "s1"
    d.mkdir(parents=True)
    (d / "session_data.json").write_text(
        json.dumps({"url": "https://youtu.be/abc", "highlights": [highlight("00:00:10,000", "00:01:15,000")]})
    )
    return d


@pytest.fixture
def stub_processor(monkeypatch):
    calls = []
    stub = types.ModuleType("clipper.clip_processor")

    def fake(**kwargs):
        calls.append(kwargs)
        return {"results": [{"output_path": "x/master.mp4"}]}

    stub.process_selected_highlights = fake
    monkeypatch.setitem(sys.modules, "clipper.clip_processor", stub)
    return calls


def test_process_with_explicit_face_fails_before_any_download(monkeypatch, session_dir, stub_processor, capsys):
    monkeypatch.setattr(cli, "mediapipe_available", lambda: False)
    # No --cookies given and none exist: the face error must come first, before
    # anything else is attempted.
    monkeypatch.chdir(session_dir)
    monkeypatch.delenv("CLIPPER_COOKIES", raising=False)

    code = cli.main(["process", str(session_dir), "--reframe", "face"])
    err = capsys.readouterr().err
    assert code == 1
    assert "clipper[face]" in err
    assert stub_processor == []  # nothing was rendered or downloaded


def test_auto_reframe_falls_back_to_centered_and_says_so(monkeypatch, session_dir, stub_processor, tmp_path, capsys):
    monkeypatch.setattr(cli, "mediapipe_available", lambda: False)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n")

    code = cli.main(["process", str(session_dir), "--cookies", str(cookies)])
    assert code == 0
    assert stub_processor[0]["options"]["reframeMode"] == "centered"
    assert "using --reframe centered" in capsys.readouterr().out


def test_auto_reframe_uses_face_tracking_when_available(monkeypatch, session_dir, stub_processor, tmp_path):
    monkeypatch.setattr(cli, "mediapipe_available", lambda: True)
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n")
    assert cli.main(["process", str(session_dir), "--cookies", str(cookies)]) == 0
    assert stub_processor[0]["options"]["reframeMode"] == "face"


def test_the_whole_package_imports_and_degrades_cleanly_without_mediapipe(tmp_path):
    # A separate interpreter where `import mediapipe` is made to fail, as on an
    # Intel Mac with no wheel. Upstream imported it at module level in portrait.py.
    code = (
        "import sys\n"
        "sys.modules['mediapipe'] = None\n"
        "import clipper.cli, clipper.clip_processor, clipper.portrait\n"
        "assert clipper.cli.mediapipe_available() is False\n"
        "try:\n"
        "    clipper.portrait.convert_to_portrait('a.mp4', 'b.mp4')\n"
        "except RuntimeError as e:\n"
        "    print('FACE-ERROR:', e)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, timeout=120
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert "FACE-ERROR:" in result.stdout and "clipper[face]" in result.stdout


def test_doctor_explains_how_to_enable_face_tracking(monkeypatch, tmp_path, capsys):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLIPPER_API_KEY", raising=False)
    monkeypatch.setattr(cli, "mediapipe_available", lambda: False)
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0  # optional, so a warning, not a failure
    assert "[WARN] mediapipe not installed" in out
    assert "pip install 'clipper[face]'" in out and "Intel" in out
