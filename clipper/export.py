"""Gather a session's finished clips into one flat, readable folder.

Clips are named ``01 - short-title.mp4`` so they are easy to pick out in Finder or the Files
app, and a ``captions.txt`` with a ready-to-paste caption for each, plus the review sheet,
lands beside them. From there, select the files and use AirDrop (macOS: Share > AirDrop), or
export straight into a folder that syncs to your phone (for example iCloud Drive).

AirDrop itself cannot be driven from a command line, so this stops at the folder.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .review import load_session_data, write_review_csv
from .social import caption_line, load_social


@dataclass(frozen=True)
class ExportResult:
    dest: Path
    clips: list[Path]
    captions: Path
    review: Path


def slugify(text: str, max_len: int = 60) -> str:
    """A filesystem-safe, lowercase ASCII slug; never empty."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:max_len].rstrip("-") or "clip"


def default_export_dir(session_dir: str | Path) -> Path:
    return Path.home() / "Desktop" / "clipper-exports" / Path(session_dir).name


def export_clips(session_dir: str | Path, dest: str | Path | None = None) -> ExportResult:
    session_dir = Path(session_dir)
    highlights = load_session_data(session_dir).get("highlights") or []
    masters = sorted((session_dir / "clips").glob("clip_*/master.mp4"))
    if not masters:
        raise ValueError(f"no rendered clips in {session_dir}. Run `clipper process` first.")

    dest = Path(dest).expanduser() if dest else default_export_dir(session_dir)
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    blocks: list[str] = []
    for master in masters:
        index = int(master.parent.name.split("_")[1])
        highlight = highlights[index] if index < len(highlights) else {}
        social = load_social(session_dir, index) or {}
        title = social.get("title") or highlight.get("title") or f"clip {index + 1}"
        target = dest / f"{index + 1:02d} - {slugify(title)}.mp4"
        shutil.copy2(master, target)
        copied.append(target)

        lines = [target.name, f"Title:   {title}"]
        if social:
            lines.append(f"Caption: {caption_line(social)}")
            tags = " ".join(f"#{t}" for t in social.get("hashtags") or [])
            for variant in (social.get("variants") or [])[1:]:
                lines.append(f"Alt ({variant.get('style', '?')}): {variant.get('caption', '')} {tags}".rstrip())
            if social.get("pinned_comment"):
                lines.append(f"Pinned comment: {social['pinned_comment']}")
            if social.get("keywords"):
                lines.append(f"Search keywords: {', '.join(social['keywords'])}")
        elif highlight.get("description"):
            lines.append(f"Caption: {highlight['description']}")
        if highlight.get("hook_text"):
            lines.append(f"Hook:    {highlight['hook_text']}")
        blocks.append("\n".join(lines))

    captions = dest / "captions.txt"
    captions.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    review = write_review_csv(session_dir, dest / "review.csv")
    return ExportResult(dest=dest, clips=copied, captions=captions, review=review)


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
