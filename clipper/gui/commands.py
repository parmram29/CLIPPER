"""Turn a request from the web page into a ``clipper`` command line.

The page is untrusted input. Nothing from it ever reaches a shell: every value is checked here, then
handed to the subprocess as its own argument. Free text goes in as ``--option=value`` so a value that
starts with a dash can never be read as a different option. ``tests/test_gui_commands.py`` feeds every
command built here through the real argument parser, so a renamed option fails a test, not a user.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

KINDS = ("find", "run", "process", "export")

# style -> (highlight profile, --drama-share override). "drama" keeps the 6-of-8 drama check;
# "wild" is for funny / chaotic / reaction clips, where "mostly arguments" is the wrong rule.
STYLES: dict[str, tuple[str, str | None]] = {
    "drama": ("viral", None),
    "wild": ("viral", "0"),
    "podcast": ("podcast", None),
}
DEFAULT_STYLE = "drama"

DEFAULT_TOP = 3
DEFAULT_FIND = 8
MAX_FIND = 20
MAX_TOP = 10
MAX_PICK_NUMBER = 50

# [0-9], not \d: \d also matches digits from other scripts, and this text becomes a folder name.
SESSION_ID = re.compile(r"[0-9]{8}_[0-9]{6}")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"})

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class RequestError(ValueError):
    """The request cannot be run. The message is written for the person, so it is safe to show."""


def clean_text(value: Any, limit: int) -> str:
    """One tidy line: control characters (including newlines) become spaces, runs of spaces collapse."""
    text = _CONTROL.sub(" ", "" if value is None else str(value))
    return " ".join(text.split())[:limit].strip()


def normalize_youtube_url(text: Any) -> str:
    """The canonical ``https://www.youtube.com/watch?v=ID`` for any normal YouTube video link.

    Playlist and timestamp parts are dropped, so pasting a link from the middle of a playlist makes
    clips from that one video, not from the whole playlist.
    """
    raw = clean_text(text, 2000)
    if not raw:
        raise RequestError("Paste a YouTube link first.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlparse(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or host not in YOUTUBE_HOSTS:
        raise RequestError(
            "That doesn't look like a YouTube link. Paste one that starts with youtube.com or youtu.be."
        )
    segments = [s for s in parts.path.split("/") if s]
    video_id = ""
    if host == "youtu.be":
        video_id = segments[0] if segments else ""
    elif segments[:1] == ["watch"]:
        video_id = (parse_qs(parts.query).get("v") or [""])[0]
    elif segments[:1] in (["shorts"], ["live"], ["embed"], ["v"]) and len(segments) > 1:
        video_id = segments[1]
    if not VIDEO_ID.fullmatch(video_id):
        raise RequestError(
            "I couldn't find a video in that link. Open the video itself on YouTube and copy its address."
        )
    return f"https://www.youtube.com/watch?v={video_id}"


def session_path(out_dir: str | Path, session_id: Any) -> Path:
    """The folder of an existing session. Only ids shaped like a session folder name are accepted."""
    text = str(session_id or "")
    if not SESSION_ID.fullmatch(text):
        raise RequestError("I don't know that session.")
    path = Path(out_dir) / "sessions" / text
    if not (path / "session_data.json").is_file():
        raise RequestError("I can't find that session on this computer.")
    return path


def _int(value: Any, what: str, low: int, high: int, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RequestError(f"{what} must be a whole number.")
    try:
        number = int(value)
    except ValueError:
        raise RequestError(f"{what} must be a whole number.") from None
    if not low <= number <= high:
        raise RequestError(f"{what} must be between {low} and {high}.")
    return number


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise RequestError("A yes/no option had a value that is neither.")


def _picks(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or not value:
        raise RequestError("Choose at least one moment to make a clip from.")
    numbers = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= MAX_PICK_NUMBER:
            raise RequestError("One of the chosen moments is not valid.")
        numbers.add(item)
    return sorted(numbers)


def _process_flags(params: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    if not _bool(params.get("captions"), True):
        flags.append("--no-captions")
    if not _bool(params.get("hook"), True):
        flags.append("--no-hook")
    background = params.get("background") or "blurred"
    if background not in ("blurred", "black"):
        raise RequestError("The background must be blurred or black.")
    flags.append(f"--background={background}")
    niche = clean_text(params.get("niche"), 100)
    if niche:
        flags.append(f"--niche={niche}")
    if _bool(params.get("export"), True):
        flags.append("--export")
    return flags


def build_command(kind: str, params: Mapping[str, Any], out_dir: str | Path) -> list[str]:
    """The arguments after ``clipper`` for one job. Raises ``RequestError`` for anything unusable."""
    if kind not in KINDS:
        raise RequestError("I don't know how to do that.")
    out_dir = Path(out_dir)

    if kind in ("find", "run"):
        url = normalize_youtube_url(params.get("url"))
        style = params.get("style") or DEFAULT_STYLE
        if style not in STYLES:
            raise RequestError("Pick one of the clip styles.")
        profile, share = STYLES[style]
        top = _int(params.get("top"), "How many clips", 1, MAX_TOP, DEFAULT_TOP)
        find = _int(params.get("find"), "How many moments to look for", 1, MAX_FIND, DEFAULT_FIND)
        argv = [kind, url, f"--clips={max(find, top) if kind == 'run' else find}", f"--profile={profile}"]
        argv.append(f"--out={out_dir}")
        if share is not None:
            argv.append(f"--drama-share={share}")
        direction = clean_text(params.get("direction"), 500)
        if direction:
            argv.append(f"--direction={direction}")
        if kind == "run":
            argv.append(f"--top={top}")
            argv += _process_flags(params)
        return argv

    session = session_path(out_dir, params.get("session"))
    if kind == "process":
        picks = ",".join(str(n) for n in _picks(params.get("picks")))
        return ["process", str(session), f"--pick={picks}", *_process_flags(params)]
    return ["export", str(session)]
