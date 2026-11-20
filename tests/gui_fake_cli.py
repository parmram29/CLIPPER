"""A stand-in for the real pipeline, so the app can be tested without YouTube or an AI.

This runs the REAL ``clipper`` command line (argument parsing, ``_process``, social copy, export, the
review sheet), and fakes only the two things that need the internet: finding highlights (which would
download captions and call the AI) and rendering clips (which would download video and run ffmpeg).
The messages it prints are copied from the real pipeline, and ``tests/test_gui_progress.py`` checks
that every message the app's progress bar relies on still exists in the real source.

Special video ids, for the tests:  FAILFAILFAI  -> "No subtitle available" failure,
                                   SLOWSLOWSLO  -> hangs until it is cancelled.
Set ``FAKE_DELAY`` (seconds, default 0.02) to slow the log down, and ``FAKE_TRENDS=offline`` to make
every live trend source fail.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper import cli, social, trend_net, trend_sources  # noqa: E402

DELAY = float(os.environ.get("FAKE_DELAY", "0.02"))
TINY_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 8  # 2072 bytes, not playable


GOOGLE_RSS = """<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><title>x</title>
<item><title>Love Is Blind</title><ht:approx_traffic>200K+</ht:approx_traffic></item>
<item><title>Weather Today</title><ht:approx_traffic>100K+</ht:approx_traffic></item></channel></rss>"""
NEWS_RSS = """<rss><channel><title>t</title>
<item><title>Love Is Blind reunion: the fight everyone is talking about - Variety</title></item>
<item><title>Love Is Blind season 9 reunion date - Deadline</title></item></channel></rss>"""
SUGGEST = json.dumps(["love is blind", ["love is blind reunion", "love is blind season 9 cast"]])


def fake_fetch_text(url, hosts, timeout=8):
    """The live trend sources, answered from memory so no test ever touches the internet."""
    if os.environ.get("FAKE_TRENDS") == "offline":
        raise trend_net.FeedError("couldn't connect (no network)")
    answers = [
        ("trends.google.com", GOOGLE_RSS),
        ("wikimedia.org", json.dumps({"items": [{"articles": [{"article": "Love_Is_Blind"}]}]})),
        ("bsky.app", json.dumps({"topics": [{"topic": "Love Is Blind"}]})),
        ("mastodon.social", json.dumps([{"name": "caturday"}])),
        ("suggestqueries", SUGGEST), ("api.bing.com", SUGGEST),
    ]
    for fragment, body in answers:
        if fragment in url:
            return body
    if any(h in url for h in ("news.google.com", "bing.com/news", "variety.com", "deadline.com", "tmz.com", "knowyourmeme.com", "ign.com", "kotaku", "polygon")):
        return NEWS_RSS
    raise trend_net.FeedError("HTTP 404", status=404)


def say(message: str) -> None:
    print(message, flush=True)
    time.sleep(DELAY)


def highlights() -> list[dict]:
    rows = [
        ("He said 'I never loved you'", "00:10:02,000", "00:11:10,000", 68, 9, 8, "argument", "I never loved you, not once."),
        ("The walk-out", "00:25:30,000", "00:26:34,000", 64, 8, 7, "wtf", "Don't follow me."),
        ("Calm chat about plans", "00:40:00,000", "00:41:05,000", 65, 6, 2, "other", "We should travel more."),
        ("Caught in a lie", "00:52:10,000", "00:53:20,000", 70, 7, 6, "scandal", "You told me you were at work."),
    ]
    out = []
    for title, start, end, seconds, virality, drama_score, category, hook in rows:
        out.append(
            {
                "title": title, "start_time": start, "end_time": end, "duration_seconds": seconds,
                "virality_score": virality, "drama_score": drama_score, "opening_score": 2, "drama_why": "a clash",
                "category": category, "hook_text": hook, "description": f"About: {title}",
                "transcript_text": hook * 10,
            }
        )
    return out


def fake_find(*, url, num_clips, subtitle_language, output_dir, cookies_path, ai, user_direction, output_language, log=None, **_):
    video_id = url.split("v=")[-1]
    stamp = datetime.now()
    session_dir = Path(output_dir) / "sessions" / stamp.strftime("%Y%m%d_%H%M%S")
    while session_dir.exists():  # two jobs in the same second
        stamp = datetime.fromtimestamp(stamp.timestamp() + 1)
        session_dir = Path(output_dir) / "sessions" / stamp.strftime("%Y%m%d_%H%M%S")
    session_dir.mkdir(parents=True)
    say(f"Session directory: {session_dir}")
    say("Downloading EN subtitle (no video)...")
    if video_id.startswith("FAILFAILFAI"):
        raise RuntimeError("No subtitle available for this video")
    say("Video: A very dramatic dinner")
    say("Channel: Some Show")
    say(f"Subtitle download success: {session_dir}/_temp/video.en.srt")
    say("Parsed transcript: 3000 lines, 90000 chars")
    say("Finding highlights with AI...")
    if video_id.startswith("SLOWSLOWSLO"):
        time.sleep(60)
    say("Window 1/2: 00:00:00 to 00:30:00")
    say("Window 2/2: 00:28:00 to 01:00:00")
    say("Kept 4 of 9 candidates")
    picks = highlights()
    say(f"Found {len(picks)} valid highlights")
    data = {
        "session_dir": str(session_dir), "url": url, "highlights": picks, "status": "highlights_found",
        "video_info": {"title": "A very dramatic dinner", "channel": "Some Show"},
        "created_at": stamp.isoformat(),
    }
    (session_dir / "session_data.json").write_text(json.dumps(data), encoding="utf-8")
    say(f"Session data saved to: {session_dir / 'session_data.json'}")
    return data


def fake_render(*, url, highlights, session_dir, options, ai):
    total = len(highlights)
    say(f"Clip padding: {1.5:.1f}s before / {2.5:.1f}s after each range")
    results = []
    for i, h in enumerate(highlights, 1):
        say(f"Processing clip {i}/{total}: {h['title']}")
        say(f"[{i}/{total}] Downloading video section 00:00:00 -> 00:01:00...")
        say(f"[{i}/{total}] Section downloaded: x.mp4")
        say(f"[{i}/{total}] Portrait conversion complete")
        say(f"[{i}/{total}] Hook generation complete")
        say(f"[{i}/{total}] Caption generation complete (12 words)")
        folder = Path(session_dir) / "clips" / f"clip_{h['_highlight_index']:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "master.mp4").write_bytes(TINY_MP4)
        say(f"[{i}/{total}] Clip saved: {folder / 'master.mp4'}")
        results.append({"clip_index": h["_highlight_index"], "output_path": str(folder / "master.mp4"), "title": h["title"]})
    say(f"All {total} clips processed successfully!")
    return {"results": results, "failed": []}


def fake_social(highlight, ai, *, language, niche=None, trends=None):
    title = highlight["title"]
    return {
        "title": f"{title} (post)",
        "caption": f"He walked in and everyone froze: {highlight['hook_text']}",
        "variants": [
            {"style": "tease", "caption": f"Wait for it: {title}"},
            {"style": "question", "caption": "Would you have stayed?"},
            {"style": "hot take", "caption": "He was right and you know it"},
        ],
        "hashtags": ["drama", "realitytv", "fyp"],
        "keywords": ["reality tv fight", "dinner drama"],
        "pinned_comment": "Team who?",
        "trend_tags_used": [t for t in ("drama", "realitytv", "fyp") if trends and t in trends.hashtags],
    }


def fake_doctor() -> int:
    has_key = bool(os.environ.get("CLIPPER_API_KEY"))
    rows = [("ok", "Python 3.13.0"), ("ok", "ffmpeg version 7.1"), ("WARN", "cookies file not found (cookies.txt); `find` and `process` need one")]
    rows.append(("ok", "AI key set") if has_key else ("FAIL", "no API key: set CLIPPER_API_KEY"))
    if "--check-ai" in sys.argv:
        rows.append(("ok", "the AI answered"))
    for level, message in rows:
        print(f"  [{level:<4}] {message}")
    return 0 if has_key else 1


def main() -> int:
    if sys.argv[1:2] == ["doctor"]:
        return fake_doctor()
    # the real command line, with only the internet-bound parts replaced
    from clipper import session as session_module

    session_module.find_highlights_only = fake_find
    cli.render_clips = fake_render
    cli.resolve_cookies = lambda *args, **kwargs: "cookies.txt"
    social.generate_social_copy = fake_social
    trend_net.fetch_text = fake_fetch_text
    trend_sources.default_ytdlp_search = lambda cookies=None: (
        lambda seed, count: [{"title": "The reunion went off #LoveIsBlind #drama", "view_count": 9000}]
    )
    os.environ["CLIPPER_LIVE_TRENDS"] = os.environ.get("FAKE_TRENDS_ON", "1")  # the stand-in always has fake feeds
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
