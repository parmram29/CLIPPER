"""Face-tracked 9:16 reframe on a real face.

The source is a 16:9 frame with a portrait photo parked at the far right, so a
plain center crop loses the face while a tracked crop must follow it. Uses the
public-domain Grace Hopper photo that ships with matplotlib as sample data.
"""

import re
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from clipper.helpers import get_face_landmarker_model_path, get_ffmpeg_path

SRC_W, SRC_H = 1280, 720
CROP_W = round(SRC_H * 9 / 16)  # width of a 9:16 window at source height = 405


def _sample_photo():
    import matplotlib

    path = Path(matplotlib.get_data_path()) / "sample_data" / "grace_hopper.jpg"
    if not path.is_file():
        pytest.skip("matplotlib sample photo not available")
    return cv2.imread(str(path))


@pytest.fixture(scope="module")
def face_model():
    try:
        return get_face_landmarker_model_path()
    except Exception as exc:  # no network and no cached model
        pytest.skip(f"MediaPipe face model unavailable: {exc}")


def _detect_faces(face_model, bgr):
    """Return the number of faces MediaPipe finds in a BGR image."""
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=face_model),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=3,
        min_face_detection_confidence=0.35,
    )
    landmarker = vision.FaceLandmarker.create_from_options(options)
    try:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        return len(result.face_landmarks)
    finally:
        landmarker.close()


@pytest.fixture(scope="module")
def right_side_face_video(tmp_path_factory, face_model):
    photo = _sample_photo()
    height = 620
    width = round(photo.shape[1] * height / photo.shape[0])
    photo = cv2.resize(photo, (width, height))

    canvas = np.full((SRC_H, SRC_W, 3), 90, dtype=np.uint8)
    x0 = SRC_W - width - 30
    canvas[50:50 + height, x0:x0 + width] = photo

    media = tmp_path_factory.mktemp("face")
    still = str(media / "canvas.png")
    cv2.imwrite(still, canvas)
    video = str(media / "src.mp4")
    cmd = [
        get_ffmpeg_path(), "-v", "error", "-y",
        "-loop", "1", "-framerate", "30", "-i", still,
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-300:]
    return video, canvas


def _frame(path, seconds):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def test_the_face_is_findable_in_the_full_frame(right_side_face_video, face_model):
    # Sanity check on the fixture itself, so the next two tests mean something.
    _, canvas = right_side_face_video
    assert _detect_faces(face_model, canvas) >= 1


def test_a_plain_center_crop_loses_the_face(right_side_face_video, face_model):
    _, canvas = right_side_face_video
    left = (SRC_W - CROP_W) // 2
    center_crop = canvas[:, left:left + CROP_W]
    assert _detect_faces(face_model, center_crop) == 0


def test_tracked_reframe_follows_the_face_into_the_portrait_crop(right_side_face_video, face_model, tmp_path):
    from clipper.portrait import convert_to_portrait

    video, _ = right_side_face_video
    out = str(tmp_path / "tracked.mp4")
    logs = []
    convert_to_portrait(video, out, logs.append)

    frame = _frame(out, 1.5)
    assert frame.shape[:2] == (1920, 1080)
    assert _detect_faces(face_model, frame) >= 1, "face should be inside the tracked 9:16 crop"
    # upstream logs e.g. "... (90 with face, 0 without = 0% no-face)"
    summary = next(m for m in logs if "Face tracking complete" in m)
    no_face_pct = int(re.search(r"(\d+)% no-face", summary).group(1))
    assert no_face_pct <= 20, f"face should be detected in most frames: {summary}"
