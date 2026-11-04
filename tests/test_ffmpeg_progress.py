"""ffmpeg's `-progress` output can say `out_time_us=N/A` (seen on a user's Mac with an
evermeet.cx snapshot build). Upstream called int() on it and the whole run died at 90%
of the portrait encode with: invalid literal for int() with base 10: 'N/A'."""

import os
import shutil
import stat
import subprocess

import cv2
import pytest

from clipper import portrait

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("out_time_us=1500000", 1.5),
        ("out_time_us=0", 0.0),
        ("  out_time_us=2000000\n", 2.0),
        ("out_time_us=N/A", None),  # the crash
        ("out_time_us=", None),
        ("out_time_us=-9223372036854775807", None),  # ffmpeg's "unset" sentinel
        ("out_time_ms=1500000", None),  # a different key
        ("frame=45", None),
        ("progress=continue", None),
        ("", None),
    ],
)
def test_progress_seconds(line, expected):
    assert portrait._progress_seconds(line) == expected


@pytest.fixture
def na_progress_ffmpeg(tmp_path):
    """A wrapper around the real ffmpeg whose progress output reports N/A, like the user's build."""
    real = shutil.which("ffmpeg")
    script = tmp_path / "ffmpeg"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'"{real}" "$@" 2> >(sed \'s/^out_time_us=.*/out_time_us=N\\/A/\' >&2)\n'
        "wait\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def source_clip(tmp_path):
    path = tmp_path / "src.mp4"
    cmd = [
        shutil.which("ffmpeg"), "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    return str(path)


def test_centered_reframe_survives_na_progress_output(na_progress_ffmpeg, source_clip, tmp_path, monkeypatch):
    monkeypatch.setattr(portrait, "get_ffmpeg_path", lambda: na_progress_ffmpeg)
    out = str(tmp_path / "out.mp4")
    logs = []

    portrait.convert_to_portrait_centered(source_clip, out, background="black", log=logs.append)

    assert os.path.getsize(out) > 0
    cap = cv2.VideoCapture(out)
    try:
        assert (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (1080, 1920)
    finally:
        cap.release()
    assert any("Centered portrait conversion complete" in m for m in logs)
