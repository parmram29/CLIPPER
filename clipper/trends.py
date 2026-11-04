"""A user-maintained file of what is trending right now, fed into the caption prompt.

There is no official data feed for TikTok trends: the Creative Center
(https://ads.tiktok.com/help/article/how-to-use-trends) is a dashboard, and scraping it
is brittle and may break the site's terms. So the trends are pasted in by hand, and the
model is told to use only what is in the file, because its own idea of "trending" is
months out of date.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TRENDS_FILE = "trends.txt"
# Trends decay quickly. After this many days the file is flagged as stale (a CLIPPER default,
# not a platform figure).
STALE_AFTER_DAYS = 14
MAX_CHARS = 1500

TEMPLATE = """\
// CLIPPER trends file. Lines starting with // are ignored.
// Paste what is trending RIGHT NOW for your niche and region, then save. Captions will only
// use a trend when it genuinely fits the clip. Refresh it every week or so: trends go stale fast.
//
// Where to look: TikTok Creative Center > Trends (hashtags, videos, creators by region and
// industry): https://ads.tiktok.com/help/article/how-to-use-trends
// Also useful: the For You page of an account in your niche, and the search bar suggestions.
//
// Delete the examples below and write your own.

Trending hashtags (region + industry):
#examplehashtag #anotherone

Formats / phrases going around (a way people title or caption this kind of clip):
- "POV: ..." style captions
- "Wait for it" / "Part 1" series framing

Topics in the news or on the timeline this week (so clips about them can say so):
- (example) a reality show finale airing this weekend

Notes (what to avoid, what your audience reacts to):
- (example) no spoilers in the first line
"""

_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)


@dataclass(frozen=True)
class Trends:
    path: Path
    text: str
    age_days: float
    hashtags: tuple[str, ...]
    stale: bool
    truncated: bool


def extract_hashtags(text: str) -> list[str]:
    """Unique lowercase ``#tags`` in order of appearance, without the ``#``."""
    tags: list[str] = []
    for tag in _HASHTAG.findall(text):
        tag = tag.lower()
        if tag not in tags:
            tags.append(tag)
    return tags


def _clean(raw: str) -> str:
    lines = [line.rstrip() for line in raw.splitlines() if not line.lstrip().startswith("//")]
    return "\n".join(lines).strip()


def load_trends(
    path: str | Path | None = None,
    *,
    now: float | None = None,
    max_age_days: float = STALE_AFTER_DAYS,
) -> Trends | None:
    """Read the trends file. Returns None if there is none (or it has no content)."""
    file = Path(path) if path else Path(DEFAULT_TRENDS_FILE)
    if not file.is_file():
        return None
    text = _clean(file.read_text(encoding="utf-8"))
    if not text:
        return None
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS].rsplit("\n", 1)[0].rstrip()
    age_days = max(0.0, ((time.time() if now is None else now) - file.stat().st_mtime) / 86400)
    return Trends(
        path=file,
        text=text,
        age_days=age_days,
        hashtags=tuple(extract_hashtags(text)),
        stale=age_days > max_age_days,
        truncated=truncated,
    )


def init_template(path: str | Path | None = None) -> Path:
    """Write the template. Refuses to overwrite an existing file."""
    file = Path(path) if path else Path(DEFAULT_TRENDS_FILE)
    if file.exists():
        raise FileExistsError(f"{file} already exists; edit it instead of re-creating it")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(TEMPLATE, encoding="utf-8")
    return file


def describe(trends: Trends | None, path: str | Path | None = None) -> str:
    """A one-line status for the command line."""
    if trends is None:
        where = Path(path) if path else Path(DEFAULT_TRENDS_FILE)
        return (
            f"No trends file ({where}): captions use general best practice only. "
            "Run `clipper trends --init` to add current trends."
        )
    age = "today" if trends.age_days < 1 else f"{trends.age_days:.0f} day(s) ago"
    line = f"Using trends from {trends.path} (updated {age}; {len(trends.hashtags)} hashtag(s))"
    if trends.stale:
        line += f". STALE: older than {STALE_AFTER_DAYS} days, so refresh it before relying on it"
    if trends.truncated:
        line += f". Only the first {MAX_CHARS} characters are used"
    return line
