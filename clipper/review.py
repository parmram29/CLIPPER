"""A review sheet for a session: one row per highlight, as a CSV.

Import it into Google Sheets (File > Import > Upload) or open it in Numbers/Excel. Fill in the
``my_rating`` column (1-10), then run ``clipper eval`` to see how well the model's
``virality_score`` agrees with your judgement.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .social import load_social

COLUMNS = [
    "clip_no",
    "rendered",
    "title",
    "category",
    "virality_score",
    "window_score",
    "my_rating",
    "notes",
    "hook_text",
    "caption",
    "hashtags",
    "start_time",
    "end_time",
    "duration_s",
    "transcript_excerpt",
    "file",
]
EXCERPT_CHARS = 300


def load_session_data(session_dir: str | Path) -> dict[str, Any]:
    path = Path(session_dir) / "session_data.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Pass a session folder created by `clipper find`.")
    return json.loads(path.read_text(encoding="utf-8"))


def build_rows(session_dir: str | Path) -> list[dict[str, Any]]:
    session_dir = Path(session_dir)
    rows = []
    for i, h in enumerate(load_session_data(session_dir).get("highlights") or []):
        master = session_dir / "clips" / f"clip_{i:03d}" / "master.mp4"
        social = load_social(session_dir, i) or {}
        excerpt = " ".join(str(h.get("transcript_text") or "").split())
        rows.append(
            {
                "clip_no": i + 1,
                "rendered": "yes" if master.is_file() else "no",
                "title": social.get("title") or h.get("title", ""),
                "category": h.get("category", ""),
                "virality_score": h.get("virality_score", ""),
                "window_score": h.get("window_score", ""),
                "my_rating": "",
                "notes": "",
                "hook_text": h.get("hook_text", ""),
                "caption": social.get("caption", ""),
                "hashtags": " ".join(f"#{t}" for t in social.get("hashtags") or []),
                "start_time": h.get("start_time", ""),
                "end_time": h.get("end_time", ""),
                "duration_s": h.get("duration_seconds", ""),
                "transcript_excerpt": excerpt[:EXCERPT_CHARS],
                "file": str(master) if master.is_file() else "",
            }
        )
    return rows


def write_review_csv(session_dir: str | Path, dest: str | Path | None = None) -> Path:
    """Write the review sheet. UTF-8 with a BOM, so Excel and Numbers read accents correctly."""
    rows = build_rows(session_dir)
    if not rows:
        raise ValueError("this session has no highlights")
    path = Path(dest) if dest else Path(session_dir) / "review.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path
