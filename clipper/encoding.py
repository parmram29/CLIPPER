"""How CLIPPER encodes video: one decision for every step that writes a video file.

Not part of upstream. Each step (the cut out of the source video, the portrait layout, the hook text, the captions,
the watermark) used to pick its own encoder, and all but one always took libx264 at its ``fast`` preset on the CPU, whatever
the computer. On a laptop that is the slowest way there is: a clip went through that encoder up to four times. This module
chooses once, for all of them:

* the computer's own hardware encoder when it works (VideoToolbox on every Mac, Intel ones included; NVENC, Quick Sync or
  AMF elsewhere), checked by actually encoding a test frame with the settings used for real;
* otherwise libx264 at ``veryfast``, about twice as quick as ``fast`` for a picture that looks the same on a phone;
* and if a hardware encoder fails in the middle of a run, the step is run again on libx264 and the hardware encoder is
  left alone for the rest of that run.

It also holds the two settings that decide how much work there is to encode: how many frames per second a clip keeps
(``CLIPPER_MAX_FPS``) and how tall the video downloaded for a clip is (``CLIPPER_MAX_HEIGHT``).
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Mapping

from . import gpu
from .constants import SUBPROCESS_FLAGS

SOFTWARE = "libx264"
SOFTWARE_ARGS = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20")
# The piece cut out of the source video is a working file: the next step reads it once and it is deleted. So it is
# written as fast as libx264 can write (a much bigger file, the same picture).
CUT_ARGS = ("-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p")

DEFAULT_MAX_FPS = 30.0
DEFAULT_MAX_HEIGHT = 1080
DEFAULT_MAX_HEIGHT_CENTERED = 720  # a 16:9 picture shown 1080 wide needs no more than 720 lines
MIN_HEIGHT, MAX_HEIGHT = 240, 2160

_lock = threading.Lock()
_state: dict[str, object] = {"failed": set(), "announced": False}


@dataclass(frozen=True)
class Encoder:
    name: str
    args: tuple[str, ...]
    hardware: bool
    reason: str = ""


def hardware_args(name: str, preset: str | None) -> tuple[str, ...]:
    """The settings for one hardware encoder."""
    if name == "h264_videotoolbox":
        return ("-c:v", name, *gpu.VIDEOTOOLBOX_ARGS)
    if name == "h264_nvenc":
        return ("-c:v", name, *(("-preset", preset) if preset else ()), "-rc", "vbr", "-cq", "23")
    return ("-c:v", name, *(("-preset", preset) if preset else ()))


def choose() -> Encoder:
    """The encoder to use now."""
    info = gpu.detect_gpu()
    found = info.get("encoder") or {}
    name = found.get("name") if found.get("available") else None
    with _lock:
        failed = set(_state["failed"])  # type: ignore[arg-type]
    if name and name not in failed:
        return Encoder(name, hardware_args(name, found.get("preset")), True, found.get("reason") or "")
    why = f"{name} failed earlier in this run" if name else (found.get("reason") or "no hardware encoder")
    return Encoder(SOFTWARE, SOFTWARE_ARGS, False, why)


def announce(encoder: Encoder, log: Callable[[str], None]) -> None:
    """Say which encoder is used, once per run, not once per step."""
    with _lock:
        if _state["announced"]:
            return
        _state["announced"] = True
    if encoder.hardware:
        log(f"Using hardware encoder: {encoder.name} ({encoder.reason})")
    else:
        log(f"Using CPU encoder: libx264 ({encoder.reason})")


def disable(name: str) -> None:
    """Stop using a hardware encoder that failed, for the rest of this run."""
    with _lock:
        _state["failed"].add(name)  # type: ignore[attr-defined]
        _state["announced"] = False  # the next step says what it uses now


def reset() -> None:
    """Forget failures and the announcement (for tests)."""
    with _lock:
        _state["failed"] = set()
        _state["announced"] = False
    gpu.reset_detection()


def run(
    build_command: Callable[[list[str]], list[str]],
    *,
    log: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess:
    """Run one ffmpeg command with the chosen encoder, and again on libx264 if a hardware encoder fails.

    ``build_command`` gets the encoder's arguments (``-c:v ...``) and returns the complete command.
    """
    log = log or (lambda m: None)
    encoder = choose()
    announce(encoder, log)
    result = _call(build_command(list(encoder.args)))
    if result.returncode != 0 and encoder.hardware:
        tail = " | ".join([ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()][-3:])
        log(f"The {encoder.name} encoder failed (exit code {result.returncode}); using libx264 instead. ffmpeg said: {tail[:300]}")
        disable(encoder.name)
        result = _call(build_command(list(SOFTWARE_ARGS)))
    return result


def _call(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS)


# --- how much there is to encode -------------------------------------------------------------------------


def max_fps(env: Mapping[str, str] | None = None) -> float:
    """The frame rate a clip is capped at: ``CLIPPER_MAX_FPS``, default 30. ``0`` or ``off`` keeps the source's.

    A 60 fps source has twice the frames to scale, blur and encode, and TikTok plays 30 fps perfectly well.
    A source at or below the cap is never changed. Anything that is not a number gives the default.
    """
    raw = ((os.environ if env is None else env).get("CLIPPER_MAX_FPS") or "").strip().lower()
    if raw in ("0", "off", "none", "no", "source"):
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_FPS
    return value if 5.0 <= value <= 120.0 else DEFAULT_MAX_FPS


def fps_filter(source_fps: float, cap: float) -> str | None:
    """``fps=30`` when the source runs faster than the cap, else None (the source is left alone)."""
    if cap and source_fps > cap + 0.5:
        return f"fps={cap:g}"
    return None


def max_height(centered: bool, env: Mapping[str, str] | None = None) -> int:
    """The tallest source video to download for a clip. ``CLIPPER_MAX_HEIGHT`` sets it for every layout; without it,
    720 for the centered layout (the video is shown 1080 wide, so 720 lines lose nothing you can see on a phone, and
    the download, the decode and every encode afterwards are about 2.25x smaller) and 1080 otherwise (a face-tracked
    crop is enlarged, so it keeps the detail)."""
    raw = ((os.environ if env is None else env).get("CLIPPER_MAX_HEIGHT") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_HEIGHT_CENTERED if centered else DEFAULT_MAX_HEIGHT
    return min(max(value, MIN_HEIGHT), MAX_HEIGHT)
