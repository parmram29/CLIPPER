"""Picks shorter than the minimum clip length are widened with the conversation around them.

This came from a real run: on a 53-minute episode the model returned 21 picks, every one between
10 and 37 seconds, although the prompt says 60-120. The length rule discarded all of them and the
run ended with "No valid highlights found in any window".
"""

import json
import re

import pytest

from clipper import highlight_finder, windowing

from conftest import clock_text, highlight, make_transcript

# The lengths (seconds) of the picks the model returned for each window of that episode.
REAL_RUN_WINDOW_1 = [31, 26, 24, 28, 23, 32, 30, 37, 30, 36]
REAL_RUN_WINDOW_2 = [18, 23, 15, 12, 12, 11, 13, 10, 10, 23, 33]

MIN, MAX = highlight_finder.MIN_CLIP_SECONDS, highlight_finder.MAX_CLIP_SECONDS


def _lines(minutes=10, step=5):
    return windowing.parse_lines(make_transcript(minutes, step))  # a line every 5 s, 4 s long


def _stamp(seconds):
    return f"{clock_text(seconds)},000"


# --- fit_to_length ------------------------------------------------------------------------------------


def test_a_short_pick_in_the_middle_gets_set_up_before_and_fallout_after():
    result = windowing.fit_to_length(_lines(), 300, 330)
    assert result == (285, 359)  # 15 s before, 29 s after: about a third / two thirds of the 42 s added
    start, end = result
    assert MIN <= end - start <= MAX and start <= 300 and end >= 330


def test_both_ends_land_on_subtitle_line_boundaries():
    lines = _lines()
    start, end = windowing.fit_to_length(lines, 303, 333)
    assert start in {line.start for line in lines}
    assert end in {line.end for line in lines}


def test_a_pick_at_the_very_start_takes_its_extra_time_after_it_instead():
    start, end = windowing.fit_to_length(_lines(), 5, 35)
    assert start == 0 and MIN <= end - start <= MAX and end >= 35


def test_a_pick_at_the_very_end_takes_its_extra_time_before_it_instead():
    lines = _lines()  # the video ends at 599 s
    start, end = windowing.fit_to_length(lines, 560, 590)
    assert end == 599 and MIN <= end - start <= MAX and start <= 560


def test_the_moment_always_stays_inside_the_widened_clip():
    lines = _lines()
    checked = 0
    for start in range(0, 580, 7):
        for length in (8, 15, 30, 45, 57):
            end = start + length
            if end > 599:
                continue
            result = windowing.fit_to_length(lines, start, end)
            assert result is not None, (start, end)
            new_start, new_end = result
            assert new_start <= start and new_end >= end, (start, end, result)
            assert MIN <= new_end - new_start <= MAX, (start, end, result)
            assert 0 <= new_start and new_end <= 599
            checked += 1
    assert checked > 300


def test_a_pick_that_is_already_long_enough_is_returned_unchanged():
    assert windowing.fit_to_length(_lines(), 100, 170) == (100, 170)


def test_a_pick_that_is_too_long_is_not_shortened_here():
    assert windowing.fit_to_length(_lines(), 100, 260) is None


@pytest.mark.parametrize(
    "case,lines,start,end",
    [
        ("no transcript", [], 10, 40),
        ("end before start", None, 40, 10),
        ("video shorter than the minimum", windowing.parse_lines(make_transcript(0.6)), 5, 25),
        ("pick beyond the end of the video", None, 5000, 5030),
    ],
)
def test_it_gives_up_instead_of_inventing_a_range(case, lines, start, end):
    assert windowing.fit_to_length(_lines() if lines is None else lines, start, end) is None, case


def test_unusually_long_subtitle_lines_cannot_push_the_clip_past_the_maximum():
    # one 40-second caption line every 50 s: snapping outward alone would overshoot
    text = "\n".join(f"[{clock_text(t)},000 - {clock_text(t + 40)},000] long caption {t}" for t in range(0, 600, 50))
    lines = windowing.parse_lines(text)
    # widening 300-340 wants 289-361, which snaps outward to the lines 250-290 and 350-390: 140 s, too long.
    # The end is pulled back to the last line end that fits, which is 340 (the original end): 250-340.
    assert windowing.fit_to_length(lines, 300, 340) == (250, 340)


# --- through the model call -----------------------------------------------------------------------------


def _call(server, transcript, num_clips=3, **kwargs):
    logs = []
    kwargs.setdefault("fit_length", windowing.length_fitter(transcript))
    result, _ = highlight_finder.find_highlights(
        transcript=transcript,
        video_info={"title": "T", "channel": "C", "description": "d"},
        num_clips=num_clips,
        api_key="k",
        base_url=server.base_url,
        model="qwen-flash",
        log=logs.append,
        **kwargs,
    )
    return result, logs


def test_short_picks_are_widened_and_kept_with_their_original_times_remembered(fake_llm):
    transcript = make_transcript(20)
    fake_llm.reply = json.dumps(
        [highlight("00:05:00,000", "00:05:30,000", title="the fight"), highlight("00:12:00,000", "00:12:20,000", title="the zinger")]
    )
    picks, logs = _call(fake_llm, transcript)

    assert [h["title"] for h in picks] == ["the fight", "the zinger"]
    for h in picks:
        assert MIN <= h["duration_seconds"] <= MAX
        s, e = windowing._span(h)
        assert e - s == pytest.approx(h["duration_seconds"], abs=0.1)
    fight = picks[0]
    assert (fight["model_start_time"], fight["model_end_time"]) == ("00:05:00,000", "00:05:30,000")
    assert windowing._span(fight)[0] <= 300 and windowing._span(fight)[1] >= 330
    assert any("Widened the fight from 30s" in m for m in logs)
    assert not any(m.startswith("Skip") for m in logs)


def test_without_a_fitter_short_picks_are_still_skipped_as_upstream_does(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:05:00,000", "00:05:30,000", title="the fight")])
    picks, logs = _call(fake_llm, make_transcript(20), fit_length=None)
    assert picks == []
    assert any("Skip the fight (30s) - outside 58-120s" in m for m in logs)


def test_picks_that_are_already_the_right_length_are_left_alone(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:05:00,000", "00:06:10,000", title="fine")])
    (pick,), logs = _call(fake_llm, make_transcript(20))
    assert (pick["start_time"], pick["end_time"]) == ("00:05:00,000", "00:06:10,000")
    assert "model_start_time" not in pick and not any("Widened" in m for m in logs)


def test_a_range_the_user_asked_for_is_never_widened(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:05:00,000", "00:05:30,000", title="what I asked for")])
    (pick,), logs = _call(fake_llm, make_transcript(20), user_direction="use 5:00 - 5:30")
    assert (pick["start_time"], pick["end_time"]) == ("00:05:00,000", "00:05:30,000")
    assert any("exact range from your direction" in m for m in logs)


def test_a_pick_that_is_too_long_is_still_skipped(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:05:00,000", "00:08:20,000", title="too long")])
    picks, logs = _call(fake_llm, make_transcript(20))
    assert picks == [] and any("Skip too long (200s) - outside 58-120s" in m for m in logs)


@pytest.mark.parametrize("bad", [{"start_time": "soon", "end_time": "later"}, {"start_time": 12, "end_time": 40}, {"title": "no times"}])
def test_picks_with_unreadable_times_do_not_break_the_widening(fake_llm, bad):
    fake_llm.reply = json.dumps([bad, highlight("00:05:00,000", "00:05:30,000", title="good one")])
    picks, logs = _call(fake_llm, make_transcript(20))
    assert [h["title"] for h in picks] == ["good one"]


def test_the_timestamps_written_back_read_as_the_same_seconds():
    for seconds in (0, 0.5, 59.999, 61.2, 3599.9, 3600, 7325.25):
        assert windowing.parse_timestamp(highlight_finder._timestamp(seconds)) == pytest.approx(seconds, abs=0.001)


# --- the real failure, end to end ------------------------------------------------------------------------


def _real_run_reply(total):
    """A model that behaves like qwen-flash did: it returns only the short moments, 10-37 s each."""

    def reply(body):
        prompt = body["messages"][0]["content"]
        if "CANDIDATES:" in prompt:
            ids = re.findall(r"^\[(\d+)\] \d+s \|", prompt, re.M)
            return json.dumps([{"id": int(i), "virality_score": 7} for i in ids])
        match = re.search(r"Transcript:\n\[(\d\d):(\d\d):(\d\d),", prompt)
        window_start = int(match[1]) * 3600 + int(match[2]) * 60 + int(match[3])
        lengths = REAL_RUN_WINDOW_1 if window_start == 0 else REAL_RUN_WINDOW_2
        picks = []
        for i, length in enumerate(lengths):
            start = window_start + 60 + 120 * i
            picks.append(highlight(_stamp(start), _stamp(start + length), title=f"moment {window_start}-{i}", score=8 - i % 3))
        return json.dumps(picks)

    return reply


def _episode(server, **kwargs):
    server.reply_fn = _real_run_reply(total=53 * 60)
    logs = []
    result = windowing.find_highlights_windowed(
        transcript=make_transcript(53),
        video_info={"title": "Episode 6", "channel": "C", "description": "d"},
        num_clips=8,
        api_key="k",
        base_url=server.base_url,
        model="qwen-flash",
        log=logs.append,
        **kwargs,
    )
    return result, logs


def test_the_failed_run_fails_exactly_as_it_did_when_nothing_is_widened(fake_llm, monkeypatch):
    monkeypatch.setattr(windowing, "length_fitter", lambda transcript: (lambda start, end: None))
    with pytest.raises(RuntimeError, match="No valid highlights found in any window"):
        _episode(fake_llm)


def test_the_same_run_now_produces_clips(fake_llm):
    (picks, _), logs = _episode(fake_llm)

    assert len(picks) == 8  # the 8 that were asked for
    for h in picks:
        s, e = windowing._span(h)
        assert MIN <= e - s <= MAX, h["title"]
        assert 0 <= s and e <= 53 * 60
    for i, a in enumerate(picks):  # widened neighbours that now overlap were merged, not both kept
        for b in picks[i + 1:]:
            assert not windowing.is_duplicate(a, b)
    assert [windowing._span(h)[0] for h in picks] == sorted(windowing._span(h)[0] for h in picks)
    assert sum("Widened" in m for m in logs) >= 16  # nearly every pick needed it
    assert not any(m.startswith("Skip") for m in logs)
