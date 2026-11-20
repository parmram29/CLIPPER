"""Turn the lines a ``clipper`` command prints into something a person can follow.

The pipeline logs plain sentences, not machine-readable events, so this reads them with patterns.
That is deliberately forgiving: a line it does not recognise changes nothing, and the real log is
always one click away in the app. ``tests/test_gui_progress.py`` pins the patterns to log lines taken
from the real pipeline, so a reworded message fails a test instead of silently freezing the bar.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Sequence

from .commands import SESSION_ID

SESSION_SAVED = re.compile(r"Session data saved to: (.+)[/\\]session_data\.json\s*$")
WINDOW = re.compile(r"Window (\d+)/(\d+):")
FOUND = re.compile(r"Found (\d+) valid highlights")
PROCESSING = re.compile(r"Processing clip (\d+)/(\d+):")
CLIP_LINE = re.compile(r"\[(\d+)/(\d+)\] (.*)")
CLIP_FAILED = re.compile(r"Clip (\d+)/(\d+) failed:")
SOCIAL = re.compile(r"Clip (\d+): ")
EXPORTED = re.compile(r"Exported (\d+) clip\(s\) to: (.+)$")

# How far through one clip each sub-step puts us (0..1).
CLIP_STEPS = (
    ("Downloading video section", 0.10, "downloading just that part of the video"),
    ("Section downloaded", 0.35, "got the video"),
    ("Portrait conversion", 0.55, "making it vertical"),
    ("Hook generation", 0.70, "adding the hook text"),
    ("Caption generation", 0.90, "adding captions"),
)

STEP_LABELS = {
    "captions": "Getting the captions",
    "moments": "Finding the best moments",
    "clips": "Making the clips",
    "words": "Writing titles and hashtags",
    "export": "Saving to your folder",
}
STEPS_FOR = {
    "find": ("captions", "moments"),
    "run": ("captions", "moments", "clips", "words", "export"),
    "process": ("clips", "words", "export"),
    "export": ("export",),
}
# How much of the bar each part of a job is worth.
WEIGHTS = {
    "find": {"find": 1.0},
    "run": {"find": 0.30, "clips": 0.55, "words": 0.10, "export": 0.05},
    "process": {"clips": 0.80, "words": 0.15, "export": 0.05},
    "export": {"export": 1.0},
}


class _State:
    def __init__(self) -> None:
        self.session: str | None = None
        self.find = 0.0  # 0..1 through finding moments
        self.window: tuple[int, int] | None = None
        self.found: int | None = None
        self.clip_total: int | None = None
        self.clip_now: int | None = None
        self.clip_step = 0.0
        self.clips_done = 0
        self.render_done = False
        self.social = 0
        self.exported_to: str | None = None
        self.headline = "Starting…"

    def _advance(self, fraction: float, headline: str | None = None) -> None:
        self.find = max(self.find, fraction)
        if headline:
            self.headline = headline

    def feed(self, raw: str) -> None:
        line = raw.strip()
        if not line:
            return
        if match := SESSION_SAVED.search(line):
            name = PurePath(match.group(1)).name
            if SESSION_ID.fullmatch(name):
                self.session = name
            self._advance(1.0)
        elif line.startswith("Session directory:"):
            self._advance(0.02, "Getting started…")
        elif line.startswith("Downloading") and "subtitle" in line:
            self._advance(0.05, "Getting the video's captions…")
        elif line.startswith("Subtitle download success"):
            self._advance(0.20)
        elif line.startswith("Parsed transcript"):
            self._advance(0.25, "Reading what is said in the video…")
        elif line.startswith("Finding highlights with AI"):
            self._advance(0.30, "Looking for the best moments…")
        elif match := WINDOW.match(line):
            i, n = int(match.group(1)), max(1, int(match.group(2)))
            self.window = (i, n)
            self._advance(0.30 + 0.55 * (i - 1) / n, f"Reading part {i} of {n} of the video…")
        elif line.startswith("Re-scored"):
            self._advance(0.90, "Comparing the moments…")
        elif line.startswith("Kept "):
            self._advance(0.92)
        elif match := FOUND.match(line):
            self.found = int(match.group(1))
            self._advance(0.95, f"Found {self.found} moments.")
        elif match := PROCESSING.match(line):
            self.clip_now, self.clip_total = int(match.group(1)), int(match.group(2))
            self.clip_step = 0.0
            self.find = 1.0
            self.headline = f"Making clip {self.clip_now} of {self.clip_total}…"
        elif match := CLIP_FAILED.match(line):
            self.clips_done += 1
            self.clip_step = 0.0
        elif match := CLIP_LINE.match(line):
            self._clip_line(match.group(3))
        elif line.startswith("Clips:") or (line.startswith("All ") and "clips processed" in line):
            self.render_done = True
            self.headline = "Writing titles and hashtags…"
        elif match := EXPORTED.match(line):
            self.exported_to = match.group(2).strip()
            self.headline = "Saved to your folder."
        elif self.render_done and (match := SOCIAL.match(line)):
            self.social += 1
            self.headline = "Writing titles and hashtags…"

    def _clip_line(self, rest: str) -> None:
        if rest.startswith("Clip saved") or rest.startswith("Already processed"):
            self.clips_done += 1
            self.clip_step = 0.0
            if self.clip_now is not None and self.clip_total is not None:
                self.headline = f"Clip {self.clip_now} of {self.clip_total} is ready."
            return
        for marker, step, words in CLIP_STEPS:
            if rest.startswith(marker):
                self.clip_step = max(self.clip_step, step)
                if self.clip_now is not None and self.clip_total is not None:
                    self.headline = f"Clip {self.clip_now} of {self.clip_total}: {words}…"
                return

    def result(self, kind: str) -> dict[str, Any]:
        total = self.clip_total or 0
        clips = min(1.0, (self.clips_done + self.clip_step) / total) if total else 0.0
        if self.render_done:
            clips = 1.0
        words = min(1.0, self.social / total) if total and self.render_done else 0.0
        parts = {
            "find": self.find,
            "clips": clips,
            "words": words,
            "export": 1.0 if self.exported_to else 0.0,
        }
        weights = WEIGHTS[kind]
        percent = round(100 * sum(weights[k] * parts[k] for k in weights))

        started = {
            "captions": self.find > 0,
            "moments": self.find >= 0.25,
            "clips": self.clip_total is not None,
            "words": self.render_done,
            "export": self.render_done or kind == "export",
        }
        finished = {
            "captions": self.find >= 0.25,
            "moments": self.find >= 1.0,
            "clips": self.render_done,
            "words": self.render_done and total > 0 and self.social >= total,
            "export": self.exported_to is not None,
        }
        steps = []
        for key in STEPS_FOR[kind]:
            state = "done" if finished[key] else "active" if started[key] else "todo"
            steps.append({"key": key, "label": STEP_LABELS[key], "state": state})
        # Only one step is "active": the first unfinished one that has started.
        seen_active = False
        for step in steps:
            if step["state"] == "active":
                if seen_active:
                    step["state"] = "todo"
                seen_active = True

        return {
            "percent": max(0, min(100, percent)),
            "headline": self.headline,
            "steps": steps,
            "session": self.session,
            "found": self.found,
            "clip": {"index": self.clip_now, "total": self.clip_total} if self.clip_total else None,
            "exported_to": self.exported_to,
        }


def summarize(lines: Sequence[str], kind: str) -> dict[str, Any]:
    """Where a job is, from everything it has printed so far."""
    state = _State()
    for line in lines:
        state.feed(line)
    return state.result(kind if kind in WEIGHTS else "run")


def find_session_id(lines: Sequence[str]) -> str | None:
    return summarize(lines, "find")["session"]


# (substrings to look for in the recent log, what to tell the person). First match wins.
HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("no subtitle available", "no subtitle file produced"),
        "This video has no captions on YouTube, so CLIPPER can't read it yet. Try another video "
        "(most have automatic captions).",
    ),
    # Claude's own problems come first: their messages mention CLIPPER_API_KEY, which the "no key" hint below also looks for
    (
        ("anthropic rejected the api key",),
        "Claude refused your key. In the .env file in the CLIPPER folder, CLIPPER_API_KEY must be the key from "
        "platform.claude.com (it starts with sk-ant-), with nothing else on the line (see Setup).",
    ),
    (
        ("out of credit",),
        "Your Anthropic account is out of credit. Add some at platform.claude.com (Billing). A Claude.ai "
        "subscription does not cover API use.",
    ),
    (
        ("monthly spend limit",),
        "Your Anthropic account has reached its spend limit. Raise it at platform.claude.com, or wait for it to reset.",
    ),
    (
        ("does not know the model",),
        "Claude doesn't know the model named in the .env file. Delete the CLIPPER_MODEL line, or set "
        "CLIPPER_MODEL=claude-opus-5-5.",
    ),
    (
        ("busy or down right now", "sending requests too fast", "took too long to answer"),
        "Claude is busy or slow right now. Wait a minute and try again.",
    ),
    (
        ("could not reach anthropic",),
        "CLIPPER couldn't reach Anthropic. Check the internet connection (and any VPN), then try again.",
    ),
    (
        ("no api key", "clipper_api_key"),
        "No AI key is set yet. Add your Claude key (from platform.claude.com) to the .env file in the CLIPPER folder (see Setup).",
    ),
    (
        ("freetieronly",),
        "Your Alibaba plan doesn't allow that model. In the .env file set CLIPPER_MODEL=qwen-flash.",
    ),
    (
        ("sign in to confirm", "http error 403", "cookies may have expired", "cookies.txt not found", "cookies file"),
        "YouTube wants a fresh login. Export a new cookies.txt from your browser into the CLIPPER folder "
        "and try again (see Setup).",
    ),
    (
        ("ffmpeg not found", "no such file or directory: 'ffmpeg'", "ffmpeg was not found"),
        "ffmpeg is missing. Run the Setup check for how to install it.",
    ),
    (
        ("unable to handle request", "certificate_verify_failed"),
        "The computer's security certificates are out of order. Run the Setup check, or see the "
        "Troubleshooting part of the README.",
    ),
)


def friendly_error(lines: Sequence[str]) -> str | None:
    """A plain-English reason a job failed, or None if all we have is the raw log."""
    recent = list(lines)[-80:]
    haystack = "\n".join(recent).lower()
    for needles, message in HINTS:
        if any(n in haystack for n in needles):
            return message
    for line in reversed(recent):
        text = line.strip()
        if text.lower().startswith("error:"):
            return text[6:].strip()[:300] or None
    return None
