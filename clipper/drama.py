"""What counts as a drama scene, and the check that most of the picks are one.

Not part of upstream. This account is about drama, so "most clips are drama" is a requirement, not a
preference: of every 8 clips at least 6 must be a confrontation, argument, insult, call-out, scandal,
shocking reveal, WTF moment or punchline that lands. The rest may be anything unless told otherwise
(``--drama-share``).

Why a separate judgement: the model that picks the clips also labels them, and it labelled calm dating
talk as drama. So every candidate is scored again, in one extra call, by a prompt that asks a narrow
question and is told that most talk is calm (see ``windowing.rerank_candidates``). This module holds the
definitions that judgement and the check share.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# A pick is a drama scene when the separate judgement scores it at least this high (0-10):
#   0-2 calm (small talk, preferences, thanks, plugs)   3-5 mild (flirting, polite disagreement)
#   6-7 drama (a real clash, accusation, put-down, shock; or a punchline that really lands)
#   8-10 fireworks
# The judge also re-labels each pick's category (drama, argument, scandal, wild, wtf, funny, punchline,
# roast, spicy, ... or other for calm talk); the label is shown in the report but the score decides.
DRAMA_MIN = 6
# Opening score (0-3) of the first ten seconds alone. At or below this the opening is flat.
OPENING_FLAT_MAX = 1
OPENING_SECONDS = 10.0
# 6 of 8.
DEFAULT_SHARE = 0.75

# Steers a second look at the video when the first one found too few drama scenes.
DRAMA_DIRECTION = (
    "Return ONLY moments where people clash: arguments, call-outs, accusations, insults, put-downs, "
    "someone caught lying or exposed, shocking confessions or reveals, walking out, jaw-drop WTF "
    "moments, and punchlines or comebacks that really land. Skip calm conversation, compliments, "
    "preferences, getting-to-know-you chat, flirting with no conflict, introductions, recaps, and any "
    "plea to like or subscribe. Open every clip on its single most dramatic line."
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def virality(highlight: Mapping[str, Any]) -> float:
    return _number(highlight.get("virality_score")) or 0.0


def drama_score(highlight: Mapping[str, Any]) -> int | None:
    """The judged drama score (0-10), or None if this pick was never judged."""
    number = _number(highlight.get("drama_score"))
    return None if number is None else max(0, min(10, int(round(number))))


def opening_score(highlight: Mapping[str, Any]) -> int | None:
    number = _number(highlight.get("opening_score"))
    return None if number is None else max(0, min(3, int(round(number))))


def is_judged(highlight: Mapping[str, Any]) -> bool:
    return drama_score(highlight) is not None


def is_drama(highlight: Mapping[str, Any]) -> bool:
    """True if this pick was judged to be a drama scene.

    A pick that was never judged is NOT counted: the only evidence left is the model's own label, and that
    is the label which called calm dating talk "drama". A check that passes on bad evidence is worse than
    none, so it fails closed and the report says to run `clipper check --rejudge`.
    """
    score = drama_score(highlight)
    return score is not None and score >= DRAMA_MIN


def has_flat_opening(highlight: Mapping[str, Any]) -> bool:
    score = opening_score(highlight)
    return score is not None and score <= OPENING_FLAT_MAX


def rank_key(highlight: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """Sort key where bigger is better: drama scenes first, then how dramatic, then how strong the
    opening is, then the generic virality score. A pick that was never judged ranks on virality alone."""
    score = drama_score(highlight)
    if score is None:
        return (0, 0, 0, virality(highlight))
    return (1 if score >= DRAMA_MIN else 0, score, opening_score(highlight) or 0, virality(highlight))


def required_count(total: int, share: float = DEFAULT_SHARE) -> int:
    """How many of ``total`` picks must be drama scenes: at least ``share`` of them (6 of 8 at 75%)."""
    if total <= 0 or share <= 0:
        return 0
    return min(total, math.ceil(share * total - 1e-9))


def parse_share(value: str | float) -> float:
    """Read ``0.75``, ``75%``, ``75`` or ``6/8`` as a fraction between 0 and 1."""
    text = str(value).strip()
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if fraction:
        top, bottom = int(fraction.group(1)), int(fraction.group(2))
        if bottom == 0 or top > bottom:
            raise ValueError(f"{text!r} is not a share between 0 and 1")
        return top / bottom
    try:
        number = float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except ValueError:
        raise ValueError(f"cannot read {text!r} as a share; use 0.75, 75% or 6/8") from None
    if number > 1 and not text.endswith("%"):
        number /= 100  # "75" means 75%
    if not 0 <= number <= 1:
        raise ValueError(f"{text!r} is not a share between 0 and 1")
    return number


@dataclass(frozen=True)
class Row:
    number: int
    title: str
    category: str
    drama: int | None
    opening: int | None
    is_drama: bool
    flat_opening: bool
    why: str


@dataclass(frozen=True)
class Report:
    total: int
    required: int
    drama_count: int
    judged: int
    share: float
    passed: bool
    rows: tuple[Row, ...]

    @property
    def flat_openings(self) -> list[int]:
        return [row.number for row in self.rows if row.flat_opening]


def check(highlights: Iterable[Mapping[str, Any]], share: float = DEFAULT_SHARE) -> Report:
    """Count the drama scenes among the picks and compare with the share they must make up."""
    picks = list(highlights)
    rows = tuple(
        Row(
            number=i,
            title=str(h.get("title", "Untitled")),
            category=str(h.get("category", "") or "?"),
            drama=drama_score(h),
            opening=opening_score(h),
            is_drama=is_drama(h),
            flat_opening=has_flat_opening(h),
            why=str(h.get("drama_why", "") or ""),
        )
        for i, h in enumerate(picks, 1)
    )
    drama_count = sum(1 for row in rows if row.is_drama)
    required = required_count(len(picks), share)
    return Report(
        total=len(picks),
        required=required,
        drama_count=drama_count,
        judged=sum(1 for h in picks if is_judged(h)),
        share=share,
        passed=bool(picks) and drama_count >= required,
        rows=rows,
    )


def format_report(report: Report) -> str:
    if report.total == 0:
        return "Drama check: there are no picks to check. FAIL"
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        f"Drama check: {report.drama_count} of {report.total} picks are drama scenes "
        f"(at least {report.required} needed, {report.share:.0%}): {verdict}"
    ]
    for row in report.rows:
        drama = "  - " if row.drama is None else f"{row.drama:>3} "
        opening = " -" if row.opening is None else f" {row.opening}"
        mark = "" if row.is_drama else "   <- not a drama scene"
        why = f"  ({row.why})" if row.why else ""
        lines.append(f"  {row.number:>2}. drama{drama} open{opening}  {row.category:<9} {row.title[:58]}{why}{mark}")
    if report.judged < report.total:
        unjudged = report.total - report.judged
        lines.append(
            f"  {unjudged} pick(s) were never scored by the separate drama judgement, so only the model's own "
            "label was available for them. `clipper check SESSION --rejudge` scores them."
        )
    if report.flat_openings:
        numbers = ", ".join(str(n) for n in report.flat_openings)
        lines.append(f"  Flat opening (the first {OPENING_SECONDS:.0f} seconds are set-up): pick(s) {numbers}.")
    if not report.passed:
        lines.append(
            "  Too few drama scenes. Try a different video or `--direction \"only arguments, call-outs and "
            "reveals\"`, or lower the bar with `--drama-share` (for example `--drama-share 0.5`)."
        )
    return "\n".join(lines)
