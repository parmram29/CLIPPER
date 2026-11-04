"""Post copy for each clip: a cover title, a caption, and hashtags.

Upstream had a title/description generator, but it lived in its desktop-app glue code
(sidecar.py), which CLIPPER dropped. This is a rewrite for the headless CLI.

The length limits below are CLIPPER's own design choices (short, hook-first copy), not
platform rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import llm

TITLE_MAX = 60
CAPTION_MAX = 150
MAX_HASHTAGS = 6
EXCERPT_CHARS = 900
SOCIAL_DIRNAME = "social"

_SYSTEM = "You write post copy for short-form video. You reply with valid JSON only."

_PROMPT = """Write post copy for one short vertical video clip, for {platform}.

Clip facts:
{facts}

Rules:
- title: at most 60 characters. It is the cover text, so lead with curiosity or conflict, and
  never promise something the clip does not deliver.
- caption: at most 150 characters, hook first, casual {language}, no hashtags inside it, and
  at most one or two emoji.
- hashtags: 3 to 6 lowercase tags, written without the # sign and without spaces, relevant to
  what actually happens. No misleading tags.
- Never invent facts, names or quotes that are not in the clip facts above.

Return ONLY JSON in exactly this shape:
{"title": "...", "caption": "...", "hashtags": ["...", "..."]}"""


def _truncate(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[: limit - 1]) + "…"


def normalize_hashtags(raw: Any) -> list[str]:
    """Lowercase, strip ``#`` and anything that is not a word character, dedupe, cap at six."""
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    tags: list[str] = []
    for item in raw or []:
        tag = re.sub(r"[^\w]", "", str(item).lower())
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_HASHTAGS]


def _facts(highlight: Mapping[str, Any]) -> str:
    parts = []
    for label, key in (
        ("Working title", "title"),
        ("Hook line", "hook_text"),
        ("Why it travels", "description"),
        ("Category", "category"),
    ):
        if highlight.get(key):
            parts.append(f"- {label}: {highlight[key]}")
    excerpt = " ".join(str(highlight.get("transcript_text") or "").split())[:EXCERPT_CHARS]
    if excerpt:
        parts.append(f"- What is said: {excerpt}")
    return "\n".join(parts) or "- (no details available)"


def generate_social_copy(
    highlight: Mapping[str, Any],
    ai: Mapping[str, Any],
    *,
    language: str = "the same language as the transcript",
    platform: str = "tiktok",
) -> dict[str, Any]:
    """Ask the model for a title, caption and hashtags, then enforce the limits in code."""
    prompt = (
        _PROMPT.replace("{platform}", platform)
        .replace("{language}", language)
        .replace("{facts}", _facts(highlight))
    )
    text, _usage = llm.chat(ai, prompt, system=_SYSTEM, temperature=0.8, max_tokens=400, json_mode=True)
    data = llm.extract_json(text)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("the model did not return a JSON object")

    return {
        "title": _truncate(data.get("title") or highlight.get("title"), TITLE_MAX),
        "caption": _truncate(
            data.get("caption") or data.get("description") or highlight.get("hook_text"), CAPTION_MAX
        ),
        "hashtags": normalize_hashtags(data.get("hashtags")),
        "platform": platform,
    }


def caption_line(copy: Mapping[str, Any]) -> str:
    """The caption and hashtags as one line, ready to paste."""
    tags = " ".join(f"#{t}" for t in copy.get("hashtags") or [])
    return f"{copy.get('caption', '')} {tags}".strip()


def social_path(session_dir: str | Path, index: int) -> Path:
    return Path(session_dir) / SOCIAL_DIRNAME / f"clip_{index:03d}.json"


def save_social(session_dir: str | Path, index: int, copy: Mapping[str, Any]) -> Path:
    path = social_path(session_dir, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(copy), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_social(session_dir: str | Path, index: int) -> dict[str, Any] | None:
    path = social_path(session_dir, index)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
