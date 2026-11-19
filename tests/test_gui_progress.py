import re
from pathlib import Path

import pytest

from clipper.gui import progress

ROOT = Path(__file__).resolve().parents[1]


def test_progress_follows_a_real_run_from_start_to_finish(gui_real_run):
    _, _, lines = gui_real_run
    percents = [progress.summarize(lines[: i + 1], "run")["percent"] for i in range(len(lines))]
    assert percents == sorted(percents)  # never goes backwards
    assert percents[0] < 5 and percents[-1] == 100
    final = progress.summarize(lines, "run")
    assert [s["state"] for s in final["steps"]] == ["done"] * 5
    assert re.fullmatch(r"\d{8}_\d{6}", final["session"])
    assert final["clip"] == {"index": 2, "total": 2} and final["found"] == 4
    assert final["exported_to"] and final["exported_to"].endswith(final["session"])


def test_the_headline_and_steps_say_where_a_run_is(gui_real_run):
    _, _, lines = gui_real_run
    after_find = lines[: next(i for i, l in enumerate(lines) if l.startswith("Session data saved to"))] + [next(l for l in lines if l.startswith("Session data saved to"))]
    found = progress.summarize(after_find, "run")
    assert found["headline"] == "Found 4 moments."
    assert [s["state"] for s in found["steps"]] == ["done", "done", "todo", "todo", "todo"]
    mid = progress.summarize(lines[: next(i for i, l in enumerate(lines) if "[2/2] Portrait conversion complete" in l) + 1], "run")
    assert mid["headline"] == "Clip 2 of 2: making it vertical…"
    assert [s["state"] for s in mid["steps"]] == ["done", "done", "active", "todo", "todo"]
    writing = progress.summarize(lines[: next(i for i, l in enumerate(lines) if l.startswith("Clips:")) + 1], "run")
    assert writing["headline"] == "Writing titles and hashtags…" and writing["steps"][2]["state"] == "done"


def test_lines_from_the_live_trend_sources_do_not_confuse_it(gui_real_run):
    _, _, lines = gui_real_run
    assert any("Google Trends:" in l for l in lines)  # the live trend step really did run in this log
    with_noise = ["Reading what's trending right now (public sources)...", "   Google Trends: 2 names", "   Bluesky trending: FAILED, took too long"]
    assert progress.summarize(lines + with_noise, "run")["percent"] == 100


def test_each_kind_of_job_counts_its_own_steps():
    process = progress.summarize(["Clip padding: 1.5s", "Processing clip 1/2: A", "[1/2] Clip saved: x", "Processing clip 2/2: B"], "process")
    assert [s["key"] for s in process["steps"]] == ["clips", "words", "export"] and 0 < process["percent"] < 80
    export = progress.summarize(["Exported 2 clip(s) to: /tmp/x/20261105_130426"], "export")
    assert export["percent"] == 100 and export["steps"][0]["state"] == "done"
    find = progress.summarize(["Session data saved to: out/sessions/20261105_130426/session_data.json"], "find")
    assert find["percent"] == 100 and find["session"] == "20261105_130426"
    assert progress.summarize([], "no-such-kind")["steps"][0]["key"] == "captions"  # an unknown kind is treated as a full run


def test_a_long_video_reports_which_part_it_is_reading():
    lines = ["Session directory: x", "Finding highlights with AI...", "Window 2/5: 00:28:00 to 00:58:00"]
    result = progress.summarize(lines, "find")
    assert result["headline"] == "Reading part 2 of 5 of the video…"
    assert result["percent"] > progress.summarize(lines[:2], "find")["percent"]


def test_a_failed_clip_still_moves_the_bar_on():
    lines = ["Processing clip 1/2: A", "Clip 1/2 failed: boom", "Processing clip 2/2: B", "[2/2] Clip saved: y"]
    assert progress.summarize(lines, "process")["percent"] >= progress.summarize(lines[:2], "process")["percent"] > 0


def test_a_session_name_that_is_not_a_session_folder_is_not_believed():
    assert progress.find_session_id(["Session data saved to: out/sessions/../../etc/session_data.json"]) is None
    assert progress.find_session_id(["Session data saved to: out/sessions/20261105_130426/session_data.json"]) == "20261105_130426"


@pytest.mark.parametrize(
    "line, expected",
    [
        ("error: No subtitle available for this video", "no captions on YouTube"),
        ("RuntimeError: No subtitle file produced", "no captions on YouTube"),
        ("error: No API key configured. Set CLIPPER_API_KEY", "No AI key is set"),
        ("openai.PermissionDeniedError: AllocationQuota.FreeTierOnly", "CLIPPER_MODEL=qwen-flash"),
        ("ERROR: Sign in to confirm you're not a bot", "fresh login"),
        ("YouTube rejected access (HTTP 403). Your cookies may have expired.", "fresh login"),
        ("FileNotFoundError: Unable to handle request: 2 unexpected errors", "security certificates"),
        ("error: ffmpeg not found on PATH", "ffmpeg is missing"),
    ],
)
def test_known_failures_get_a_plain_sentence(line, expected):
    assert expected in progress.friendly_error(["some earlier line", line, "last line"])


def test_an_unknown_failure_falls_back_to_its_own_error_line_or_to_nothing():
    assert progress.friendly_error(["ok", "error: something specific broke", "more"]) == "something specific broke"
    assert progress.friendly_error(["ok", "just noise"]) is None
    assert progress.friendly_error(["error: " + "x" * 500])[:5] == "xxxxx" and len(progress.friendly_error(["error: " + "x" * 500])) == 300


# Every message the progress bar relies on must still exist in the real pipeline's source. If someone
# rewords one, this fails, instead of the bar silently freezing.
PIPELINE_MESSAGES = [
    ("clipper/session.py", 'Session directory: '),
    ("clipper/session.py", "subtitle (no video)"),
    ("clipper/session.py", "Subtitle download success"),
    ("clipper/session.py", "Parsed transcript"),
    ("clipper/session.py", "Finding highlights with AI"),
    ("clipper/session.py", "valid highlights"),
    ("clipper/session.py", "Session data saved to: "),
    ("clipper/windowing.py", "Window {label}:"),
    ("clipper/windowing.py", "Re-scored"),
    ("clipper/windowing.py", "Kept "),
    ("clipper/clip_processor.py", "Processing clip {i}/{total}:"),
    ("clipper/clip_processor.py", "Downloading video section"),
    ("clipper/clip_processor.py", "Section downloaded"),
    ("clipper/clip_processor.py", "Portrait conversion"),
    ("clipper/clip_processor.py", "Hook generation"),
    ("clipper/clip_processor.py", "Caption generation"),
    ("clipper/clip_processor.py", "Clip saved:"),
    ("clipper/clip_processor.py", "Already processed"),
    ("clipper/clip_processor.py", "clips processed"),
    ("clipper/cli.py", 'print("\\nClips:")'),
    ("clipper/cli.py", "Clip {k}/{total} failed:"),
    ("clipper/cli.py", "Exported {len(result.clips)} clip(s) to:"),
    ("clipper/cli.py", "Clip {index + 1}: {copy['title']}"),
]


@pytest.mark.parametrize("path, text", PIPELINE_MESSAGES)
def test_the_progress_bar_only_relies_on_messages_the_pipeline_really_prints(path, text):
    assert text in (ROOT / path).read_text(encoding="utf-8"), f"{path} no longer contains {text!r}"
