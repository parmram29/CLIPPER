"""Long videos: split into overlapping windows, merge the picks, re-score on one scale."""

import json
import re

import pytest

from clipper import highlight_finder, llm, windowing

from conftest import clock_text, highlight, make_transcript


# --- splitting ---------------------------------------------------------------


def test_a_short_transcript_is_one_unchanged_window():
    transcript = make_transcript(20)
    (window,) = windowing.split_transcript(transcript)
    assert window.text == transcript


def test_windows_trigger_only_when_clearly_longer_than_one_window():
    # default window is 30 min; the trigger is 1.25x that = 37.5 min
    assert len(windowing.split_transcript(make_transcript(37))) == 1
    assert len(windowing.split_transcript(make_transcript(38))) > 1


def test_a_long_transcript_splits_into_overlapping_windows_that_lose_nothing():
    transcript = make_transcript(100)  # 6000 s
    windows = windowing.split_transcript(transcript)

    assert [w.start for w in windows] == [0, 1680, 3360, 5040]
    for earlier, later in zip(windows, windows[1:]):
        assert earlier.start + windowing.DEFAULT_WINDOW_SECONDS - later.start == windowing.DEFAULT_OVERLAP_SECONDS

    seen = set()
    for w in windows:
        lines = windowing.parse_lines(w.text)
        assert max(l.start for l in lines) < w.start + windowing.DEFAULT_WINDOW_SECONDS
        assert min(l.start for l in lines) >= w.start
        seen.update(l.raw for l in lines)
    assert seen == set(transcript.splitlines())  # every line is in at least one window


@pytest.mark.parametrize("window,overlap", [(0, 0), (600, 600), (600, 900), (600, -1)])
def test_invalid_window_settings_are_rejected(window, overlap):
    with pytest.raises(ValueError, match="window length"):
        windowing.split_transcript(make_transcript(100), window, overlap)


def test_text_without_timestamps_is_passed_through_whole():
    (window,) = windowing.split_transcript("just some words\nno timestamps here")
    assert window.text.startswith("just some words")


# --- duplicates and selection ------------------------------------------------


def _h(start, end, score, title="t"):
    return highlight(f"{clock_text(start)},000", f"{clock_text(end)},000", title=title, score=score)


def test_overlapping_picks_are_duplicates_and_the_higher_score_survives():
    a = _h(100, 165, 6, "low")
    b = _h(110, 175, 9, "high")  # 55 s of a 65 s clip overlap
    assert windowing.is_duplicate(a, b)
    assert [h["title"] for h in windowing.dedupe([a, b])] == ["high"]


def test_a_small_overlap_is_not_a_duplicate():
    a = _h(100, 165, 6)
    b = _h(160, 225, 6)  # 5 s overlap of 65 s = 8%, under the 20% threshold
    assert not windowing.is_duplicate(a, b)
    assert len(windowing.dedupe([a, b])) == 2


def test_touching_clips_are_not_duplicates():
    assert not windowing.is_duplicate(_h(0, 65, 5), _h(65, 130, 5))


def test_select_best_takes_the_top_scores_and_returns_them_in_time_order():
    pool = [_h(1000, 1065, 5, "c"), _h(100, 165, 9, "a"), _h(500, 565, 7, "b"), _h(3000, 3065, 3, "d")]
    assert [h["title"] for h in windowing.select_best(pool, 3)] == ["a", "b", "c"]


def test_select_best_never_returns_two_copies_of_one_moment():
    pool = [_h(100, 165, 9, "x"), _h(105, 170, 8, "x-dup"), _h(900, 965, 7, "y")]
    assert [h["title"] for h in windowing.select_best(pool, 3)] == ["x", "y"]


def test_junk_scores_do_not_crash_selection():
    pool = [_h(100, 165, "n/a", "junk"), _h(500, 565, 7, "ok")]
    assert windowing.select_best(pool, 1)[0]["title"] == "ok"


# --- re-scoring across windows ----------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ([{"id": 0, "virality_score": 7}, {"id": 1, "virality_score": 3}], {0: 7, 1: 3}),
        ({"scores": [{"id": 0, "virality_score": 8}]}, {0: 8}),
        ([{"id": 0, "virality_score": 99}, {"id": 1, "virality_score": -4}], {0: 10, 1: 1}),  # clamped
        ([{"id": 0, "virality_score": "6.6"}], {0: 7}),  # rounded
        ([{"id": 9, "virality_score": 5}], {}),  # id out of range
        ([{"virality_score": 5}, {"id": "x", "virality_score": 5}, "nonsense"], {}),
        ("not a list", {}),
    ],
)
def test_parse_scores(value, expected):
    assert windowing._parse_scores(value, 2) == expected


def _candidates():
    return [
        {**_h(100, 165, 6, "first"), "category": "drama"},
        {**_h(900, 965, 6, "second"), "category": "funny"},
        {**_h(1700, 1765, 6, "third"), "category": "wtf"},
    ]


def _lines():
    return windowing.parse_lines(make_transcript(60))


def _ai(server):
    return {"api_key": "k", "base_url": server.base_url, "model": "qwen-flash"}


def test_rerank_asks_once_with_the_rubric_and_excerpts_and_applies_the_scores(fake_llm):
    fake_llm.reply = json.dumps(
        [{"id": 0, "virality_score": 9}, {"id": 1, "virality_score": 4}, {"id": 2, "virality_score": 7}]
    )
    rescored, usage = windowing.rerank_candidates(_candidates(), _lines(), _ai(fake_llm))

    assert [h["virality_score"] for h in rescored] == [9, 4, 7]
    assert usage == {"prompt_tokens": 11, "completion_tokens": 22}
    (request,) = fake_llm.requests
    prompt = request["body"]["messages"][0]["content"]
    assert "HOOK (0-3)" in prompt and "SHAREABILITY (0-2)" in prompt  # the shared rubric
    assert "title: second" in prompt and "words at 900" in prompt  # candidate + transcript excerpt


def test_rerank_does_not_mutate_its_input(fake_llm):
    fake_llm.reply = json.dumps([{"id": i, "virality_score": 1} for i in range(3)])
    original = _candidates()
    windowing.rerank_candidates(original, _lines(), _ai(fake_llm))
    assert [h["virality_score"] for h in original] == [6, 6, 6]


@pytest.mark.parametrize(
    "reply",
    ["sorry, I cannot do that", "[]", json.dumps([{"id": 0, "virality_score": 9}])],  # unusable / too few
)
def test_rerank_keeps_existing_scores_when_the_reply_is_unusable(fake_llm, reply):
    fake_llm.reply = reply
    logs = []
    result, _ = windowing.rerank_candidates(_candidates(), _lines(), _ai(fake_llm), log=logs.append)
    assert [h["virality_score"] for h in result] == [6, 6, 6]
    assert any("keeping per-window scores" in m for m in logs)


def test_rerank_survives_an_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm, "chat", boom)
    logs = []
    result, usage = windowing.rerank_candidates(_candidates(), _lines(), {"api_key": "k", "model": "m"}, log=logs.append)
    assert [h["virality_score"] for h in result] == [6, 6, 6]
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0}
    assert any("network down" in m for m in logs)


def test_rerank_with_a_single_candidate_makes_no_call(fake_llm):
    one = _candidates()[:1]
    result, _ = windowing.rerank_candidates(one, _lines(), _ai(fake_llm))
    assert result == one and fake_llm.requests == []


# --- end to end ---------------------------------------------------------------

TOTAL = 100 * 60


def windowed_reply(total=TOTAL, fail_window_start=None, rerank_reply=None):
    """A fake model: two 65 s picks per window (one early, one 'late' that the next window
    also finds in the overlap), and a rerank that loves 'late' picks."""

    def reply(body):
        prompt = body["messages"][0]["content"]
        if "CANDIDATES:" in prompt:
            if rerank_reply is not None:
                return rerank_reply
            entries = re.findall(r"^\[(\d+)\] \d+s \| [^|]* \| title: (.*?) \| hook:", prompt, re.M)
            return json.dumps(
                [{"id": int(i), "virality_score": 9 if title.startswith("late") else 4} for i, title in entries]
            )
        m = re.search(r"Transcript:\n\[(\d\d):(\d\d):(\d\d),", prompt)
        start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if start == fail_window_start:
            return "this is not json at all"
        picks = []
        for offset, label in ((60, "early"), (1740, "late")):
            if start + offset + 65 <= total:
                picks.append(
                    highlight(
                        f"{clock_text(start + offset)},000",
                        f"{clock_text(start + offset + 65)},000",
                        title=f"{label}@{start}",
                        score=6,
                    )
                )
        return json.dumps(picks)

    return reply


def _find(server, num_clips=3, transcript=None, **kwargs):
    logs = []
    result = windowing.find_highlights_windowed(
        transcript=transcript or make_transcript(100),
        video_info={"title": "Long Stream", "channel": "C", "description": "d"},
        num_clips=num_clips,
        api_key="k",
        base_url=server.base_url,
        model="qwen-flash",
        log=logs.append,
        **kwargs,
    )
    return result, logs


def test_a_long_video_is_scored_per_window_then_reranked_once(fake_llm):
    fake_llm.reply_fn = windowed_reply()
    (picks, usage), logs = _find(fake_llm)

    assert len(fake_llm.requests) == 5  # 4 windows + 1 re-scoring call
    prompts = [r["body"]["messages"][0]["content"] for r in fake_llm.requests]
    assert "CANDIDATES:" in prompts[-1] and all("CANDIDATES:" not in p for p in prompts[:-1])

    # the re-scoring call lists each moment once: the 8 raw picks (2 per window) include 3 that
    # the next window found again in the overlap, and those are dropped before paying to score them
    entries = re.findall(r"^\[\d+\] \d+s \| [^|]* \| title: (.*?) \| hook:", prompts[-1], re.M)
    assert sorted(entries) == ["early@0", "late@0", "late@1680", "late@3360"]

    # each window prompt holds only its own slice of the transcript, and says which part it is
    second = prompts[1]
    assert "words at 1700" in second and "words at 1000" not in second
    assert "part 2/4 of a longer video" in second

    assert [h["title"] for h in picks] == ["late@0", "late@1680", "late@3360"]  # best scores, in time order
    assert {h["virality_score"] for h in picks} == {9}
    assert all(h["window_score"] == 6 for h in picks)  # the original per-window score is kept
    assert usage == {"prompt_tokens": 55, "completion_tokens": 110}  # 5 requests x (11, 22)
    assert any("Re-scored" in m for m in logs)


def test_the_same_moment_found_by_two_windows_is_returned_once(fake_llm):
    fake_llm.reply_fn = windowed_reply()
    (picks, _), _ = _find(fake_llm, num_clips=8)
    spans = [(windowing._span(h)) for h in picks]
    for i, a in enumerate(picks):
        for b in picks[i + 1:]:
            assert not windowing.is_duplicate(a, b)
    assert spans == sorted(spans)


def test_without_rerank_the_window_scores_are_used_and_no_extra_call_is_made(fake_llm):
    fake_llm.reply_fn = windowed_reply()
    (picks, _), _ = _find(fake_llm, rerank=False)
    assert len(fake_llm.requests) == 4
    assert len(picks) == 3 and {h["virality_score"] for h in picks} == {6}


def test_a_broken_rerank_does_not_lose_the_picks(fake_llm):
    fake_llm.reply_fn = windowed_reply(rerank_reply="I'm sorry, I can't help with that")
    (picks, _), logs = _find(fake_llm)
    assert len(picks) == 3
    assert any("keeping per-window scores" in m for m in logs)


def test_one_failed_window_is_skipped_and_the_rest_still_count(fake_llm):
    fake_llm.reply_fn = windowed_reply(fail_window_start=1680)
    (picks, _), logs = _find(fake_llm)
    assert picks
    assert any("Window 2/4 failed" in m for m in logs)
    assert not any(h["window"] == 1 for h in picks)


def test_if_every_window_fails_it_says_so(fake_llm):
    fake_llm.reply = "not json"
    with pytest.raises(RuntimeError, match="No valid highlights found in any window"):
        _find(fake_llm)


def test_a_short_video_takes_the_single_call_path_with_no_rerank(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:02:00,000", "00:03:10,000", title="only")])
    (picks, _), _ = _find(fake_llm, num_clips=1, transcript=make_transcript(20))
    assert [h["title"] for h in picks] == ["only"]
    assert len(fake_llm.requests) == 1
    assert "CANDIDATES:" not in fake_llm.requests[0]["body"]["messages"][0]["content"]
    assert "part 1/" not in fake_llm.requests[0]["body"]["messages"][0]["content"]


def test_windowed_search_calls_the_upstream_finder_for_every_window(fake_llm, monkeypatch):
    calls = []
    real = highlight_finder.find_highlights

    def spy(**kwargs):
        calls.append(kwargs["num_clips"])
        return real(**kwargs)

    monkeypatch.setattr(highlight_finder, "find_highlights", spy)
    fake_llm.reply_fn = windowed_reply()
    _find(fake_llm, num_clips=3)
    assert calls == [2, 2, 2, 2]  # ceil(3 * 2 / 4 windows) = 2 candidates per window
