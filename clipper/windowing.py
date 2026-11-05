"""Find highlights in long videos by splitting the transcript into overlapping windows.

Not part of upstream, which sends the whole transcript in a single prompt. Short videos
still take that path, unchanged. For long ones:

1. split the transcript into windows (default 30 min, overlapping by 2 min so a moment on
   a boundary is seen whole by at least one window);
2. ask for candidates in each window, and widen any pick that came back shorter than the
   minimum clip length with the conversation around it (models often return only the moment);
3. drop duplicates (the same moment found by two windows);
4. re-score every surviving candidate in ONE call, so a 7 from a dull window and a 7 from
   a lively one are comparable. Without this, each window's scores only make sense inside
   that window. With the drama check on (``drama_share``), that same call also judges how
   much real drama each candidate has and how strong its first ten seconds are, and if too
   few candidates are drama scenes the video is searched again for confrontations only;
5. keep the best ``num_clips`` that do not overlap, in chronological order (drama scenes
   first when the drama check is on).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import drama, highlight_finder, llm
from .srt_parser import parse_timestamp
from .viral_prompt import CATEGORIES, VIRALITY_RUBRIC

LogFn = Callable[[str], None]

DEFAULT_WINDOW_SECONDS = 30 * 60
DEFAULT_OVERLAP_SECONDS = 2 * 60
# A video only gets windowed when it is clearly longer than one window.
WINDOW_TRIGGER_FACTOR = 1.25
# Ask for this many times the clips wanted, then keep the best after re-scoring.
CANDIDATE_FACTOR = 2
# Two picks are "the same moment" when they overlap by more than this share of the shorter one.
DUPLICATE_OVERLAP_FRACTION = 0.2
EXCERPT_CHARS = 600
# A pick shorter than the minimum clip length is widened to about this long (the prompt asks for 60-90 s).
TARGET_CLIP_SECONDS = 72
# At most this many seconds of the added time go BEFORE the pick; the rest goes after it. The pick starts
# where the model put its hook, so a long lead-in would bury the opening under set-up (it did: 15 s).
LEAD_IN_SECONDS = 3.0
# With the drama check on, ask for more candidates (there must be enough drama to choose from) but
# never more than a 30-minute window can hold, and give the judge more of each clip to read.
DRAMA_CANDIDATE_FACTOR = 3
MAX_CANDIDATES_PER_WINDOW = 12
DRAMA_EXCERPT_CHARS = 900

_LINE_RE = re.compile(
    r"^\[(\d{2,}:\d{2}:\d{2}[,.]\d{3}) - (\d{2,}:\d{2}:\d{2}[,.]\d{3})\]\s?(.*)$"
)


@dataclass(frozen=True)
class Line:
    start: float
    end: float
    text: str
    raw: str


@dataclass(frozen=True)
class Window:
    index: int
    start: float
    end: float
    text: str


def parse_lines(transcript: str) -> list[Line]:
    """Parse upstream's ``[HH:MM:SS,mmm - HH:MM:SS,mmm] text`` transcript lines."""
    lines = []
    for raw in transcript.splitlines():
        match = _LINE_RE.match(raw)
        if match:
            lines.append(
                Line(parse_timestamp(match.group(1)), parse_timestamp(match.group(2)), match.group(3), raw)
            )
    lines.sort(key=lambda line: line.start)
    return lines


def clock(seconds: float) -> str:
    total = int(max(0, seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def split_transcript(
    transcript: str,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
) -> list[Window]:
    """Split a transcript into overlapping windows, or return one window if it is short."""
    if window_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= window_seconds:
        raise ValueError("window length must be positive and larger than the overlap")

    lines = parse_lines(transcript)
    if not lines:
        return [Window(0, 0.0, 0.0, transcript)]

    first = lines[0].start
    last = max(line.end for line in lines)
    if last - first <= window_seconds * WINDOW_TRIGGER_FACTOR:
        return [Window(0, first, last, transcript)]

    step = window_seconds - overlap_seconds
    windows: list[Window] = []
    t0 = first
    while True:
        t1 = t0 + window_seconds
        chosen = [line.raw for line in lines if t0 <= line.start < t1]
        if chosen:
            windows.append(Window(len(windows), t0, min(t1, last), "\n".join(chosen)))
        if t1 >= last:
            break
        t0 += step
    return windows


# ---------------------------------------------------------------------------
# Picks that are too short
# ---------------------------------------------------------------------------


def fit_to_length(
    lines: list[Line],
    start: float,
    end: float,
    *,
    minimum: float = highlight_finder.MIN_CLIP_SECONDS,
    maximum: float = highlight_finder.MAX_CLIP_SECONDS,
    target: float = TARGET_CLIP_SECONDS,
    lead_in_seconds: float = LEAD_IN_SECONDS,
) -> tuple[float, float] | None:
    """Widen the pick ``start``-``end`` with the transcript around it, to about ``target`` seconds.

    At most ``lead_in_seconds`` of the added time goes before the pick, so the clip still opens
    on the moment the model chose as its hook; the rest goes after it (the fallout). Whatever
    would hang over the start or end of the video moves to the other side. Both ends then snap
    outward to subtitle-line boundaries so no sentence is cut in half, and the original moment
    always stays inside. Returns None when that cannot be done: no transcript, a pick that lies
    outside it, a video shorter than ``minimum``, or a pick that is already longer than ``maximum``.
    """
    if not lines or end <= start:
        return None
    floor = min(line.start for line in lines)
    ceiling = max(line.end for line in lines)
    duration = end - start
    if duration >= minimum:
        return (start, end) if duration <= maximum else None
    if ceiling - floor < minimum or start < floor - 1 or end > ceiling + 1:
        return None

    extra = max(target, minimum) - duration
    before = min(lead_in_seconds, extra)
    new_start = start - before
    new_end = end + (extra - before)
    if new_start < floor:  # no room before the pick: take it after
        new_end += floor - new_start
        new_start = floor
    if new_end > ceiling:  # no room after it: take it before
        new_start -= new_end - ceiling
        new_end = ceiling
    new_start = max(new_start, floor)

    starts = [line.start for line in lines if line.start <= new_start]
    ends = [line.end for line in lines if line.end >= new_end]
    snapped_start = min(max(starts) if starts else floor, start)
    snapped_end = max(min(ends) if ends else ceiling, end)

    if snapped_end - snapped_start > maximum:  # unusually long subtitle lines: pull the end back
        fits = [line.end for line in lines if end <= line.end <= snapped_start + maximum]
        if not fits:
            return None
        snapped_end = max(fits)
    if snapped_end - snapped_start < minimum:
        return None
    return snapped_start, snapped_end


def length_fitter(transcript: str) -> highlight_finder.FitFn:
    """A ``fit_length`` for ``highlight_finder.find_highlights`` that uses the whole transcript."""
    lines = parse_lines(transcript)
    return lambda start, end: fit_to_length(lines, start, end)


# ---------------------------------------------------------------------------
# Candidates: de-duplication and selection
# ---------------------------------------------------------------------------


def _span(highlight: Mapping[str, Any]) -> tuple[float, float]:
    return parse_timestamp(highlight["start_time"]), parse_timestamp(highlight["end_time"])


def _score(highlight: Mapping[str, Any]) -> float:
    try:
        return float(highlight.get("virality_score", 0))
    except (TypeError, ValueError):
        return 0.0


def is_duplicate(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """True when two highlights cover substantially the same footage."""
    s1, e1 = _span(a)
    s2, e2 = _span(b)
    overlap = min(e1, e2) - max(s1, s2)
    if overlap <= 0:
        return False
    return overlap > DUPLICATE_OVERLAP_FRACTION * min(e1 - s1, e2 - s2)


def _best_first(h: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    """Sort key, best first: drama scenes, how dramatic, how strong the opening, then the generic
    score (which alone decides when nothing was judged), then the earlier moment."""
    return tuple(-x for x in drama.rank_key(h)) + (_span(h)[0],)  # type: ignore[return-value]


def dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the better one of every pair of duplicates (the more dramatic, else the higher-scoring)."""
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=_best_first):
        if not any(is_duplicate(candidate, other) for other in kept):
            kept.append(candidate)
    return kept


def select_best(candidates: list[dict[str, Any]], num_clips: int) -> list[dict[str, Any]]:
    """The ``num_clips`` best non-duplicate candidates, in chronological order."""
    return sorted(dedupe(candidates)[:num_clips], key=lambda h: _span(h)[0])


# ---------------------------------------------------------------------------
# Re-scoring across windows
# ---------------------------------------------------------------------------


def excerpt_for(lines: list[Line], start: float, end: float, max_chars: int = EXCERPT_CHARS) -> str:
    text = " ".join(line.text for line in lines if line.end >= start and line.start <= end).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


_RERANK_INTRO = (
    "You are scoring candidate short-form video clips on ONE shared scale, so clips found in "
    "different parts of a long video can be ranked against each other."
)
_RERANK_FORMAT = (
    '\n\nReturn ONLY a JSON array with one object per candidate: '
    '[{"id":0,"virality_score":7},{"id":1,"virality_score":4}]. '
    "Every id must appear exactly once. Scores are whole numbers from 1 to 10. "
    "No prose and no markdown."
)


def _parse_scores(value: Any, count: int) -> dict[int, int]:
    if isinstance(value, dict):  # e.g. {"scores": [...]}
        value = next((v for v in value.values() if isinstance(v, list)), [])
    scores: dict[int, int] = {}
    for item in value if isinstance(value, list) else []:
        try:
            idx = int(item["id"])
            score = int(round(float(item["virality_score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < count:
            scores[idx] = max(1, min(10, score))
    return scores


# The drama judge. It asks a narrow question of every candidate in one call, and says plainly that most
# talk is calm, because the model that picked the clips had called calm dating talk "drama".
_JUDGE_INTRO = (
    "You are the editor of a viral reality-TV DRAMA account on TikTok. Below are candidate clips cut from "
    "a long video. Judge each one honestly and strictly, on ONE shared scale, so clips from different "
    "parts of the video can be ranked against each other. The question for every clip: would a stranger "
    "scrolling past STOP for this?"
)
_JUDGE_RUBRIC = """\
DRAMA (0-10): how much real conflict or shock is in the clip.
* 0-2 calm: small talk, introductions, thanks, stating a preference or a plan, describing a type,
  compliments, recaps, a plea to like or subscribe.
* 3-5 mild: flirting, polite disagreement, an awkward pause, a complaint said calmly.
* 6-7 drama: a real disagreement, an accusation or call-out, a put-down or insult, someone caught out
  or lying, a pointed question nobody wants to answer, a clear shock, a punchline that really lands.
* 8-10 fireworks: a shouting match, threats, a big confession or reveal, betrayal, someone storming
  off, a line that makes you say "WHAT?!".
Two people calmly talking about feelings, plans or what they want in a partner is a 1-3, however sweet.
Most candidates should score between 2 and 6. Give 8 or more only when it is truly explosive.

OPENING (0-3): judge ONLY the "opening" text, the first ten seconds of the clip.
* 0 set-up, small talk or throat-clearing  * 1 something is brewing
* 2 the conflict or the shocking line is already there  * 3 it starts mid-explosion

CATEGORY: exactly one of: __CATEGORIES__. Use "other" for calm talk.

VIRALITY (1-10): overall pull: how hard it grabs, how shareable it is, whether it ends on its payoff."""
_JUDGE_FORMAT = (
    '\n\nReturn ONLY a JSON array with one object per candidate: '
    '[{"id":0,"drama":8,"opening":2,"virality_score":7,"category":"argument","why":"accusation, then a threat"}]. '
    "Every id must appear exactly once. drama is 0-10, opening is 0-3, virality_score is 1-10, all whole "
    "numbers; why is at most 10 words and has no double quotes. No prose and no markdown."
)


def _parse_judgements(value: Any, count: int) -> dict[int, dict[str, Any]]:
    """Read the drama judge's reply: for each candidate id, whichever of drama, opening, virality_score,
    category and why are usable. Anything unreadable is left out, and numbers are clamped to their range."""
    if isinstance(value, dict):  # e.g. {"scores": [...]}
        value = next((v for v in value.values() if isinstance(v, list)), [])
    judged: dict[int, dict[str, Any]] = {}
    for item in value if isinstance(value, list) else []:
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= idx < count:
            continue
        entry: dict[str, Any] = {}
        for key, low, high in (("drama", 0, 10), ("opening", 0, 3), ("virality_score", 1, 10)):
            try:
                entry[key] = max(low, min(high, int(round(float(item[key])))))
            except (KeyError, TypeError, ValueError):
                pass
        category = str(item.get("category", "")).strip().lower()
        if category in CATEGORIES:
            entry["category"] = category
        why = " ".join(str(item.get("why") or "").replace('"', "'").split())[:90]
        if why:
            entry["why"] = why
        if entry:
            judged[idx] = entry
    return judged


def rerank_candidates(
    candidates: list[dict[str, Any]],
    lines: list[Line],
    ai: Mapping[str, Any],
    *,
    log: LogFn | None = None,
    with_drama: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Re-score every candidate in one call. On any failure, keep the existing scores.

    With ``with_drama`` the same call also judges each candidate's drama (0-10) and the strength of
    its first ten seconds (0-3), and re-labels its category; those land in ``drama_score``,
    ``opening_score``, ``drama_why`` and ``category`` (the model's first label is kept as
    ``category_first_pass``).
    """
    log = log or (lambda m: None)
    if len(candidates) < (1 if with_drama else 2):
        return candidates, dict(llm.ZERO_USAGE)

    entries = []
    for i, h in enumerate(candidates):
        start, end = _span(h)
        head = (
            f"[{i}] {end - start:.0f}s | {h.get('category', '?')} | title: {h.get('title', '')} | "
            f"hook: {h.get('hook_text', '')} | "
        )
        if with_drama:
            opening = excerpt_for(lines, start, start + drama.OPENING_SECONDS, 240)
            entries.append(head + f"opening: {opening} | excerpt: {excerpt_for(lines, start, end, DRAMA_EXCERPT_CHARS)}")
        else:
            entries.append(head + f"excerpt: {excerpt_for(lines, start, end)}")

    if with_drama:
        rubric = _JUDGE_RUBRIC.replace("__CATEGORIES__", ", ".join(CATEGORIES))
        prompt = _JUDGE_INTRO + "\n\n" + rubric + "\n\nCANDIDATES:\n" + "\n".join(entries) + _JUDGE_FORMAT
    else:
        prompt = (
            _RERANK_INTRO + "\n\n" + VIRALITY_RUBRIC + "\n\nCANDIDATES:\n" + "\n".join(entries) + _RERANK_FORMAT
        )

    judgements: dict[int, dict[str, Any]] = {}
    try:
        if with_drama:
            text, usage = llm.chat(ai, prompt, temperature=0.1, max_tokens=110 * len(candidates) + 100)
            judgements = _parse_judgements(llm.extract_json(text), len(candidates))
            counted = sum(1 for j in judgements.values() if "drama" in j)
        else:
            text, usage = llm.chat(ai, prompt, temperature=0.2, max_tokens=60 * len(candidates) + 100)
            scores = _parse_scores(llm.extract_json(text), len(candidates))
            counted = len(scores)
    except Exception as exc:  # noqa: BLE001 - a failed re-score must not lose the picks
        log(f"Cross-window re-scoring failed ({str(exc)[:160]}); keeping per-window scores")
        return candidates, dict(llm.ZERO_USAGE)

    if counted * 2 < len(candidates):
        log(f"Re-scoring returned scores for only {counted}/{len(candidates)} candidates; keeping per-window scores")
        return candidates, usage

    rescored = []
    for i, h in enumerate(candidates):
        copy = dict(h)
        if with_drama:
            judged = judgements.get(i, {})
            if "virality_score" in judged:
                copy["virality_score"] = judged["virality_score"]
            if "drama" in judged:
                copy["drama_score"] = judged["drama"]
            if "opening" in judged:
                copy["opening_score"] = judged["opening"]
            if "why" in judged:
                copy["drama_why"] = judged["why"]
            if "category" in judged:
                if judged["category"] != copy.get("category"):
                    copy["category_first_pass"] = copy.get("category")
                copy["category"] = judged["category"]
        elif i in scores:
            copy["virality_score"] = scores[i]
        rescored.append(copy)
    suffix = ", with drama and opening judged" if with_drama else ""
    log(f"Re-scored {counted} candidates on one shared scale{suffix}")
    return rescored, usage


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def find_highlights_windowed(
    *,
    transcript: str,
    video_info: Mapping[str, Any] | None,
    num_clips: int,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str | None = None,
    temperature: float | None = None,
    user_direction: str | None = None,
    output_language: str | None = None,
    log: LogFn | None = None,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
    rerank: bool = True,
    drama_share: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Drop-in for ``highlight_finder.find_highlights`` that handles long videos.

    ``drama_share`` turns the drama check on (0.75 means 6 of every 8 clips must be drama scenes):
    every candidate is judged for drama and for how strong its first ten seconds are, drama scenes
    are chosen first, and if the first look finds too few, the video is searched again for
    confrontations only. Leave it None for the plain behaviour (the ``podcast`` profile, your own
    prompt, or ``--drama-share 0``). A direction you give yourself is never overridden.
    """
    log = log or (lambda m: None)
    drama_on = drama_share is not None
    common = dict(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt=system_prompt,
        temperature=temperature,
        user_direction=user_direction,
        output_language=output_language,
        log=log,
        fit_length=length_fitter(transcript),  # built from the whole video, not one window
    )
    if drama_on:
        common["keep_all"] = True  # rank every valid candidate instead of cutting at the model's own order

    windows = split_transcript(transcript, window_seconds, overlap_seconds)
    if len(windows) == 1 and not drama_on:
        return highlight_finder.find_highlights(
            transcript=transcript, video_info=dict(video_info or {}), num_clips=num_clips, **common
        )

    factor = DRAMA_CANDIDATE_FACTOR if drama_on else CANDIDATE_FACTOR
    per_window = max(2, math.ceil(num_clips * factor / len(windows)))
    if drama_on:
        per_window = min(per_window, MAX_CANDIDATES_PER_WINDOW)
    if len(windows) > 1:
        log(
            f"Long video: {len(windows)} windows of ~{window_seconds / 60:.0f} min "
            f"({overlap_seconds / 60:.0f} min overlap), up to {per_window} candidates each"
        )

    usage = dict(llm.ZERO_USAGE)
    last_error: Exception | None = None

    def collect(direction: str | None, note: str = "") -> list[dict[str, Any]]:
        """Ask every window for candidates (``direction`` replaces the user's, for the second look)."""
        nonlocal last_error
        found_all: list[dict[str, Any]] = []
        options = dict(common)
        if direction is not None:
            options["user_direction"] = direction
        for window in windows:
            label = f"{window.index + 1}/{len(windows)}"
            info = dict(video_info or {})
            if len(windows) > 1:
                log(f"Window {label}: {clock(window.start)} to {clock(window.end)}{note}")
                info["description"] = (
                    f"[This is part {label} of a longer video, covering {clock(window.start)} to "
                    f"{clock(window.end)}. Timestamps are absolute.] {info.get('description') or ''}"
                ).strip()
            try:
                found, window_usage = highlight_finder.find_highlights(
                    transcript=window.text, video_info=info, num_clips=per_window, **options
                )
            except RuntimeError as exc:
                last_error = exc
                log(f"Window {label} failed ({str(exc)[:160]}); continuing with the other windows")
                continue
            llm.add_usage(usage, window_usage)
            for h in found:
                h["window"] = window.index
                h["window_score"] = h.get("virality_score")
                found_all.append(h)
        return found_all

    pool = collect(None)
    if not pool:
        raise RuntimeError(
            "No valid highlights found in any window."
            + (f" Last error: {last_error}" if last_error else "")
        )

    pool = dedupe(pool)
    lines = parse_lines(transcript)
    ai = {"api_key": api_key, "base_url": base_url, "model": model}
    if drama_on:
        pool, judge_usage = rerank_candidates(pool, lines, ai, log=log, with_drama=True)
        llm.add_usage(usage, judge_usage)
        needed = drama.required_count(num_clips, drama_share)
        have = sum(1 for h in pool if drama.is_drama(h))
        if any(drama.is_judged(h) for h in pool) and have < needed and not (user_direction or "").strip():
            log(f"Only {have} drama scene(s) so far and {needed} are needed; looking again for confrontations only")
            again = dedupe(collect(drama.DRAMA_DIRECTION, " (confrontations only)"))
            fresh = [h for h in again if not any(is_duplicate(h, other) for other in pool)]
            if fresh:
                fresh, judge_usage = rerank_candidates(fresh, lines, ai, log=log, with_drama=True)
                llm.add_usage(usage, judge_usage)
                pool = dedupe(pool + fresh)
    elif rerank and len(pool) > 1:
        pool, rerank_usage = rerank_candidates(pool, lines, ai, log=log)
        llm.add_usage(usage, rerank_usage)

    selected = select_best(pool, num_clips)
    log(f"Kept {len(selected)} of {len(pool)} candidates")
    if drama_on:
        report = drama.check(selected, drama_share)
        log(
            f"Drama check: {report.drama_count} of {report.total} picks are drama scenes "
            f"({report.required} needed): {'PASS' if report.passed else 'FAIL'}"
        )
    return selected, usage
