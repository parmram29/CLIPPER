"""The video stages that run after highlights are chosen, on a synthetic clip.

Each test compares real pixels, so a stage that silently does nothing fails.
"""

import subprocess

import cv2
import numpy as np
import pytest

from clipper.helpers import get_ffmpeg_path


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "src.mp4"
    cmd = [
        get_ffmpeg_path(), "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"ffmpeg unavailable: {result.stderr[-200:]}")
    return str(path)


def _dims(path):
    cap = cv2.VideoCapture(path)
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()


def _frame_at(path, seconds):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok, f"could not read frame at {seconds}s from {path}"
    return frame


def _mean_abs_diff(a, b):
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


@pytest.mark.parametrize("background", ["black", "blurred"])
def test_centered_reframe_produces_1080x1920(source_clip, tmp_path, background):
    from clipper.portrait import convert_to_portrait_centered

    out = str(tmp_path / f"portrait_{background}.mp4")
    convert_to_portrait_centered(source_clip, out, background=background)
    assert _dims(out) == (1080, 1920)

    frame = _frame_at(out, 1.0)
    top_bar = frame[:200, :, :]  # 16:9 source scaled to 1080 wide leaves ~656px bars top and bottom
    if background == "black":
        assert top_bar.mean() < 2, "expected solid black bars"
    else:
        assert top_bar.mean() > 10, "expected a blurred copy of the picture behind the video"


def test_blurred_and_black_backgrounds_actually_differ(source_clip, tmp_path):
    from clipper.portrait import convert_to_portrait_centered

    black = str(tmp_path / "b.mp4")
    blur = str(tmp_path / "l.mp4")
    convert_to_portrait_centered(source_clip, black, background="black")
    convert_to_portrait_centered(source_clip, blur, background="blurred")
    assert _mean_abs_diff(_frame_at(black, 1.0), _frame_at(blur, 1.0)) > 5


def test_captions_are_drawn_onto_the_frame(source_clip, tmp_path):
    from clipper.caption_generator import generate_captions_from_words

    words = [{"word": w, "start": 0.2 + i * 0.4, "end": 0.55 + i * 0.4}
             for i, w in enumerate("CAPTIONS SHOULD BE VISIBLE HERE".split())]
    out = str(tmp_path / "captioned.mp4")
    generate_captions_from_words(source_clip, out, words)

    assert _dims(out) == _dims(source_clip)
    assert _mean_abs_diff(_frame_at(source_clip, 1.0), _frame_at(out, 1.0)) > 0.5


def test_captions_with_no_words_pass_the_video_through(source_clip, tmp_path):
    from clipper.caption_generator import generate_captions_from_words

    out = str(tmp_path / "unchanged.mp4")
    generate_captions_from_words(source_clip, out, [])
    assert _mean_abs_diff(_frame_at(source_clip, 1.0), _frame_at(out, 1.0)) < 1.0


def test_hook_overlay_shows_at_the_start_then_disappears(source_clip, tmp_path):
    from clipper.hook_generator import generate_hook

    out = str(tmp_path / "hooked.mp4")
    generate_hook(source_clip, "THIS IS THE HOOK LINE", out, duration=2.0)

    assert _dims(out) == _dims(source_clip)
    during = _mean_abs_diff(_frame_at(source_clip, 0.5), _frame_at(out, 0.5))
    after = _mean_abs_diff(_frame_at(source_clip, 3.2), _frame_at(out, 3.2))
    assert during > 0.5, "hook text should be visible in the first seconds"
    assert after < during / 2, "hook text should be gone after its duration"
