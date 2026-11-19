"""Gather a session's finished clips into one flat, readable folder.

Clips are named ``01 - short-title.mp4`` so they are easy to pick out in Finder or the Files
app. Beside each one goes a matching ``01 - short-title.txt`` with that clip's title, hook,
captions, hashtags and where in the source video it came from, so you can always tell which
hashtags belong to which clip. A ``captions.txt`` holding all of them, plus the review sheet,
lands in the same folder. From there, select the files and use AirDrop (macOS: Share >
AirDrop), or export straight into a folder that syncs to your phone (for example iCloud Drive).

AirDrop itself cannot be driven from a command line, so this stops at the folder.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .review import load_session_data, write_review_csv
from .social import caption_line, load_social


@dataclass(frozen=True)
class ExportResult:
    dest: Path
    clips: list[Path]
    captions: Path
    review: Path
    notes: list[Path] = field(default_factory=list)  # the matching .txt beside each clip


def slugify(text: str, max_len: int = 60) -> str:
    """A filesystem-safe, lowercase ASCII slug; never empty."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:max_len].rstrip("-") or "clip"


def default_export_dir(session_dir: str | Path) -> Path:
    return Path.home() / "Desktop" / "clipper-exports" / Path(session_dir).name


def _clock(value: Any) -> str:
    """``00:26:56,000`` -> ``00:26:56``: the milliseconds are noise for a person."""
    return str(value or "").strip().split(",")[0].split(".")[0]


def _source_line(session: Mapping[str, Any], highlight: Mapping[str, Any]) -> str:
    """Where the clip came from (link, time range, channel), or "" if none of that is known."""
    span = " - ".join(t for t in (_clock(highlight.get("start_time")), _clock(highlight.get("end_time"))) if t)
    info = session.get("video_info")
    channel = str(info.get("channel") or "").strip() if isinstance(info, Mapping) else ""
    parts = [
        str(session.get("url") or "").strip(),
        f"at {span}" if span else "",
        f"(channel: {channel})" if channel else "",
    ]
    text = " ".join(p for p in parts if p)
    return f"Source:  {text}" if text else ""


def _block(
    name: str,
    *,
    title: str,
    social: Mapping[str, Any],
    highlight: Mapping[str, Any],
    session: Mapping[str, Any],
    session_dir: Path,
) -> str:
    """Everything needed to post one clip, headed by the video's file name.

    This exact text is both that clip's own ``.txt`` and its entry in ``captions.txt``.
    """
    lines = [name, f"Title:   {title}"]
    if highlight.get("hook_text"):
        # the first words on screen, so a clip can also be recognised by sight
        lines.append(f"Hook:    {highlight['hook_text']}")
    if social:
        tags = " ".join(f"#{t}" for t in social.get("hashtags") or [])
        lines.append(f"Caption: {caption_line(social)}")
        if tags:
            lines.append(f"Hashtags: {tags}")
        for variant in (social.get("variants") or [])[1:]:
            lines.append(f"Alt ({variant.get('style', '?')}): {variant.get('caption', '')} {tags}".rstrip())
        if social.get("pinned_comment"):
            lines.append(f"Pinned comment: {social['pinned_comment']}")
        if social.get("keywords"):
            lines.append(f"Search keywords: {', '.join(social['keywords'])}")
    else:
        if highlight.get("description"):
            lines.append(f"Caption: {highlight['description']}")
        lines.append(f"Note:    no hashtags yet. Write them with: clipper social {shlex.quote(str(session_dir))}")
    source = _source_line(session, highlight)
    if source:
        lines.append(source)
    return "\n".join(lines)


def export_clips(session_dir: str | Path, dest: str | Path | None = None) -> ExportResult:
    session_dir = Path(session_dir)
    session = load_session_data(session_dir)
    highlights = session.get("highlights") or []
    masters = sorted((session_dir / "clips").glob("clip_*/master.mp4"))
    if not masters:
        raise ValueError(f"no rendered clips in {session_dir}. Run `clipper process` first.")

    dest = Path(dest).expanduser() if dest else default_export_dir(session_dir)
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    notes: list[Path] = []
    blocks: list[str] = []
    for master in masters:
        index = int(master.parent.name.split("_")[1])
        highlight = highlights[index] if index < len(highlights) else {}
        social = load_social(session_dir, index) or {}
        title = social.get("title") or highlight.get("title") or f"clip {index + 1}"
        target = dest / f"{index + 1:02d} - {slugify(title)}.mp4"
        shutil.copy2(master, target)
        copied.append(target)

        block = _block(
            target.name,
            title=title,
            social=social,
            highlight=highlight,
            session=session,
            session_dir=session_dir,
        )
        blocks.append(block)
        note = target.with_suffix(".txt")
        note.write_text(block + "\n", encoding="utf-8")
        notes.append(note)

    captions = dest / "captions.txt"
    captions.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    review = write_review_csv(session_dir, dest / "review.csv")
    return ExportResult(dest=dest, clips=copied, captions=captions, review=review, notes=notes)


def reveal(path: str | Path) -> bool:
    """Open a folder in Finder / the file manager. Returns False if that was not possible."""
    if sys.platform == "darwin":
        command = ["open", str(path)]
    elif sys.platform.startswith("linux"):
        command = ["xdg-open", str(path)]
    else:
        return False
    try:
        return subprocess.run(command, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
