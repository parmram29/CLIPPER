"""What the app shows about sessions: read from the folders ``clipper`` already writes.

Nothing here changes anything on disk. Everything sent to the page is plain data (text, numbers,
lists), never a file path the page could ask for, so the page cannot be pointed at other files.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .. import drama, export
from ..social import load_social
from .commands import SESSION_ID

SESSION_FILE = "session_data.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    if value != value:  # NaN
        return None
    return int(value) if float(value).is_integer() else round(float(value), 1)


def _text_list(value: Any) -> list[str]:
    return [str(v) for v in value if isinstance(v, (str, int, float))] if isinstance(value, (list, tuple)) else []


def _created(session_id: str) -> str:
    try:
        return datetime.strptime(session_id, "%Y%m%d_%H%M%S").isoformat(timespec="minutes")
    except ValueError:
        return ""


def master_path(out_dir: str | Path, session_id: str, index: int) -> Path | None:
    """The finished clip's file, if it exists. Only a valid session id and a plain number get through."""
    if not SESSION_ID.fullmatch(str(session_id)) or isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return None
    path = Path(out_dir) / "sessions" / session_id / "clips" / f"clip_{index:03d}" / "master.mp4"
    return path if path.is_file() else None


def _social_view(copy: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not copy:
        return None
    variants = [
        {"style": str(v.get("style") or ""), "caption": str(v.get("caption") or "")}
        for v in (copy.get("variants") or [])
        if isinstance(v, Mapping)
    ]
    return {
        "title": str(copy.get("title") or ""),
        "caption": str(copy.get("caption") or ""),
        "variants": variants,
        "hashtags": _text_list(copy.get("hashtags")),
        "trend_tags": _text_list(copy.get("trend_tags_used")),  # the ones that came from the live trend sources
        "pinned_comment": str(copy.get("pinned_comment") or ""),
        "keywords": _text_list(copy.get("keywords")),
    }


def list_sessions(out_dir: str | Path) -> list[dict[str, Any]]:
    """Every session on disk, newest first, with just enough to draw a card."""
    root = Path(out_dir) / "sessions"
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not SESSION_ID.fullmatch(folder.name) or not folder.is_dir():
            continue
        data = _read_json(folder / SESSION_FILE)
        if data is None:
            continue
        info = data.get("video_info") if isinstance(data.get("video_info"), Mapping) else {}
        highlights = data.get("highlights") if isinstance(data.get("highlights"), list) else []
        made = sum(1 for i in range(len(highlights)) if (folder / "clips" / f"clip_{i:03d}" / "master.mp4").is_file())
        found.append(
            {
                "id": folder.name,
                "created": _created(folder.name),
                "url": str(data.get("url") or ""),
                "title": str(info.get("title") or ""),
                "channel": str(info.get("channel") or ""),
                "picks": len(highlights),
                "made": made,
            }
        )
    return found


def read_session(out_dir: str | Path, session_id: str) -> dict[str, Any] | None:
    """Everything the page needs to show one session, or None if it does not exist."""
    if not SESSION_ID.fullmatch(str(session_id)):
        return None
    folder = Path(out_dir) / "sessions" / session_id
    data = _read_json(folder / SESSION_FILE)
    if data is None:
        return None
    info = data.get("video_info") if isinstance(data.get("video_info"), Mapping) else {}
    highlights = [h for h in (data.get("highlights") or []) if isinstance(h, Mapping)]

    picks = []
    for index, h in enumerate(highlights):
        rendered = master_path(out_dir, session_id, index) is not None
        picks.append(
            {
                "number": index + 1,
                "index": index,
                "title": str(h.get("title") or "Untitled"),
                "hook": str(h.get("hook_text") or ""),
                "description": str(h.get("description") or ""),
                "start": export.clock(h.get("start_time")),
                "end": export.clock(h.get("end_time")),
                "seconds": _number(h.get("duration_seconds")),
                "category": str(h.get("category") or ""),
                "virality": _number(h.get("virality_score")),
                "drama": drama.drama_score(h),
                "drama_why": str(h.get("drama_why") or ""),
                "opening": drama.opening_score(h),
                "rendered": rendered,
                "video": f"/media/{session_id}/{index}" if rendered else None,
                "social": _social_view(load_social(folder, index)),
            }
        )

    ranking: list[int] = []
    if highlights:
        from ..cli import top_indices  # imported here: the rest of cli is not needed to list sessions

        ranking = top_indices(highlights, len(highlights))

    judged = [h for h in highlights if drama.is_judged(h)]
    export_dir = export.default_export_dir(folder)
    exported = len(list(export_dir.glob("*.mp4"))) if export_dir.is_dir() else 0
    return {
        "id": session_id,
        "created": _created(session_id),
        "url": str(data.get("url") or ""),
        "video": {"title": str(info.get("title") or ""), "channel": str(info.get("channel") or "")},
        "picks": picks,
        "ranking": ranking,
        "drama": {
            "judged": len(judged),
            "scenes": sum(1 for h in judged if drama.is_drama(h)),
            "total": len(highlights),
        },
        "exported": exported,
    }
