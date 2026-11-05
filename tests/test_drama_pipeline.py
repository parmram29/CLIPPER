"""The drama check end to end, against a fake model that behaves like the one in the real run.

That model listed calm dating-show talk first, scored it higher than the confrontations, labelled it
"drama", and ignored the instruction to return 60-120 seconds. Here it is scripted the same way:
``Script`` returns, for each window, candidates of the kinds you give it ("calm" or "fight") in that order,
and judges them when the second call asks. What the tests check is the pipeline's behaviour, not the
taste of a real model; a real run prints its own Drama check for that.
"""

import json
import re

import pytest

from clipper import drama, highlight_finder, windowing

from conftest import clock_text, highlight, make_transcript, write_srt

EPISODE = 53  # minutes: two 30-minute windows, as in the real run
STEER = "Return ONLY moments where people clash"  # the start of drama.DRAMA_DIRECTION


def _stamp(seconds):
    return f"{clock_text(seconds)},000"


def is_fight(h):
    """The truth in these tests: the scripted candidates are named for what they are."""
    return h["title"].startswith("fight")


class Script:
    """first: candidate kinds per window in the model's own order. again: the same, for the second look
    that asks for confrontations only. judge: 'ok' or 'garbage'."""

    def __init__(self, first, again=(), judge="ok", step=130):
        self.first, self.again, self.judge, self.step = first, again, judge, step
        self.window_prompts, self.judge_prompts, self.steered_prompts = [], [], []

    def __call__(self, body):
        prompt = body["messages"][0]["content"]
        if "CANDIDATES:" in prompt:
            self.judge_prompts.append(prompt)
            return self._judge(prompt)
        window_start = self._window_start(prompt)
        if STEER in prompt:
            self.steered_prompts.append(prompt)
            kinds, base, step = self.again, window_start + 800, self.step
        else:
            self.window_prompts.append(prompt)
            kinds, base, step = self.first, window_start + 60, self.step
        picks = []
        for i, kind in enumerate(kinds):
            start = base + step * i
            title = f"{kind} {window_start}-{i}" + (" (steered)" if STEER in prompt else "")
            if kind == "plea":
                title = f"plea {window_start}-{i}: like and subscribe"
            # the model's own generic scores: calm talk scores higher than the fights, as it did
            virality = {"calm": 8, "plea": 9, "fight": 6}[kind] - (i % 2)
            h = highlight(_stamp(start), _stamp(start + 30), title=title, score=virality, hook=f"hook {i}")
            h["category"] = "drama"  # it called everything drama
            picks.append(h)
        return json.dumps(picks)

    @staticmethod
    def _window_start(prompt):
        m = re.search(r"Transcript:\n\[(\d\d):(\d\d):(\d\d),", prompt)
        return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])

    def _judge(self, prompt):
        if self.judge == "garbage":
            return "I'm sorry, I can't help with that"
        out = []
        for idx, title in re.findall(r"^\[(\d+)\] \d+s \| [^|]* \| title: (.*?) \| hook:", prompt, re.M):
            if title.startswith("fight"):
                out.append({"id": int(idx), "drama": 8, "opening": 2, "virality_score": 7, "category": "argument", "why": "clash"})
            elif title.startswith("plea"):
                out.append({"id": int(idx), "drama": 0, "opening": 0, "virality_score": 3, "category": "other", "why": "channel plea"})
            else:
                out.append({"id": int(idx), "drama": 2, "opening": 0, "virality_score": 7, "category": "other", "why": "small talk"})
        return json.dumps(out)


def _find(server, script, *, num_clips=8, minutes=EPISODE, **kwargs):
    server.reply_fn = script
    logs = []
    kwargs.setdefault("drama_share", drama.DEFAULT_SHARE)
    result = windowing.find_highlights_windowed(
        transcript=make_transcript(minutes),
        video_info={"title": "Episode", "channel": "C", "description": "d"},
        num_clips=num_clips,
        api_key="k",
        base_url=server.base_url,
        model="qwen-flash",
        log=logs.append,
        **kwargs,
    )
    return result, logs


# --- the failure this was built for --------------------------------------------------------------------------------------


def test_before_the_drama_check_the_dramatic_tail_of_the_models_list_never_reached_the_ranking(fake_llm):
    # five calm picks first, then six fights, in each window: the plain pipeline kept the first eight per window
    script = Script(first=["calm"] * 5 + ["fight"] * 6)
    (picks, _), _ = _find(fake_llm, script, drama_share=None, rerank=False)
    assert len(picks) == 8
    assert sum(is_fight(h) for h in picks) == 0  # nothing dramatic got through; this is the regression


def test_with_the_check_on_the_same_model_gives_a_clear_majority_of_drama(fake_llm):
    script = Script(first=["calm"] * 5 + ["fight"] * 6)
    (picks, _), logs = _find(fake_llm, script)

    assert len(picks) == 8
    assert sum(is_fight(h) for h in picks) >= 6  # 6 of 8
    assert drama.check(picks).passed
    assert all(drama.is_judged(h) for h in picks)
    assert any(l.startswith("Drama check: ") and l.endswith("PASS") for l in logs)
    assert len(fake_llm.requests) == 3  # two windows and ONE judging call; nothing needed looking at again


def test_a_plea_to_subscribe_does_not_make_it_into_the_picks(fake_llm):
    script = Script(first=["plea", "calm", "calm", "fight", "fight", "fight", "fight"])
    (picks, _), _ = _find(fake_llm, script)
    assert not any("plea" in h["title"] for h in picks)
    assert sum(is_fight(h) for h in picks) >= 6


def test_when_exactly_six_scenes_exist_the_other_two_are_filled_with_the_best_of_the_rest(fake_llm):
    script = Script(first=["calm"] * 4 + ["fight"] * 3, again=["fight"] * 5)  # 3 fights per window = 6 in the episode
    (picks, _), _ = _find(fake_llm, script)
    assert len(picks) == 8
    assert sum(is_fight(h) for h in picks) == 6
    assert drama.check(picks).passed and drama.check(picks).drama_count == 6
    assert script.steered_prompts == []  # six are needed and six were found: no second look


def test_drama_scenes_at_the_very_end_of_the_models_list_are_not_cut_off(fake_llm):
    # 12 calm picks and then 3 fights per window: the model returns 15 where 12 were asked for (it is
    # told to add 3 spare), and the plain pipeline kept only the first 12 in the model's own order
    script = Script(first=["calm"] * 12 + ["fight"] * 3, step=100)  # 15 picks 100 s apart fit in a window
    (picks, _), logs = _find(fake_llm, script)
    assert sum(is_fight(h) for h in picks) == 6
    assert script.steered_prompts == [] and len(fake_llm.requests) == 3

    fake_llm.requests.clear()
    plain_script = Script(first=["calm"] * 12 + ["fight"] * 3, step=100)
    (plain, _), _ = _find(fake_llm, plain_script, drama_share=None, rerank=False)
    assert sum(is_fight(h) for h in plain) == 0  # which is why they were lost


def test_a_judge_that_returns_no_drama_scores_counts_as_a_failed_judging(fake_llm):
    class NoDrama(Script):
        def _judge(self, prompt):
            ids = re.findall(r"^\[(\d+)\] ", prompt, re.M)
            return json.dumps([{"id": int(i), "virality_score": 5} for i in ids])  # scores, but none for drama

    script = NoDrama(first=["calm"] * 5 + ["fight"] * 6)
    (picks, _), logs = _find(fake_llm, script)
    assert not any(drama.is_judged(h) for h in picks)
    assert script.steered_prompts == []
    assert any(l.startswith("Re-scoring returned scores for only 0/") for l in logs)


def test_of_two_overlapping_picks_the_more_dramatic_one_is_kept_not_the_higher_scoring():
    calm = highlight("00:10:00,000", "00:11:10,000", title="calm but 9", score=9)
    fight = highlight("00:10:30,000", "00:11:40,000", title="fight but 5", score=5)
    calm.update(drama_score=2, opening_score=0)
    fight.update(drama_score=8, opening_score=2)
    assert windowing.is_duplicate(calm, fight)
    assert [h["title"] for h in windowing.dedupe([calm, fight])] == ["fight but 5"]
    assert [h["title"] for h in windowing.select_best([calm, fight], 1)] == ["fight but 5"]


def test_the_picks_come_back_in_time_order_for_the_editor(fake_llm):
    (picks, _), _ = _find(fake_llm, Script(first=["calm"] * 3 + ["fight"] * 8))
    starts = [windowing._span(h)[0] for h in picks]
    assert starts == sorted(starts)


# --- looking again when drama is scarce ---------------------------------------------------------------------------------------------


def test_when_the_first_look_finds_too_little_drama_it_looks_again_for_confrontations_only(fake_llm):
    script = Script(first=["calm"] * 4 + ["fight"], again=["fight"] * 5)  # 2 fights in the pool, 6 are needed
    (picks, _), logs = _find(fake_llm, script)

    assert any("Only 2 drama scene(s) so far and 6 are needed; looking again" in l for l in logs)
    assert len(script.steered_prompts) == 2  # one second look per window
    whole = " ".join(drama.DRAMA_DIRECTION.split())
    assert all(whole in " ".join(p.split()) for p in script.steered_prompts)  # the complete instruction, not a fragment
    assert all(whole not in " ".join(p.split()) for p in script.window_prompts)  # and only in the second look
    assert len(script.judge_prompts) == 2  # the new candidates were judged too
    assert len(fake_llm.requests) == 2 + 1 + 2 + 1
    assert sum(is_fight(h) for h in picks) >= 6 and drama.check(picks).passed
    assert any("(steered)" in h["title"] for h in picks)


def test_no_second_look_when_enough_drama_was_found_the_first_time(fake_llm):
    script = Script(first=["calm"] * 2 + ["fight"] * 4, again=["fight"] * 5)
    _find(fake_llm, script)
    assert script.steered_prompts == []


def test_a_direction_you_gave_yourself_is_never_overridden(fake_llm):
    script = Script(first=["calm"] * 4 + ["fight"], again=["fight"] * 5)
    (picks, _), logs = _find(fake_llm, script, user_direction="skip the first five minutes")
    assert script.steered_prompts == []
    assert len(fake_llm.requests) == 3
    assert all("skip the first five minutes" in p for p in script.window_prompts)
    assert not drama.check(picks).passed  # and the shortfall is reported, not hidden
    assert any(l.startswith("Drama check: ") and l.endswith("FAIL") for l in logs)


def test_if_the_second_look_finds_nothing_new_the_run_still_finishes(fake_llm):
    script = Script(first=["calm"] * 4 + ["fight"], again=[])
    (picks, _), _ = _find(fake_llm, script)
    assert len(picks) == 8 and sum(is_fight(h) for h in picks) == 2


# --- when the judging itself fails ----------------------------------------------------------------------------------------------------


def test_if_the_judge_fails_the_picks_still_come_back_and_nothing_is_searched_again(fake_llm):
    script = Script(first=["calm"] * 5 + ["fight"] * 6, judge="garbage")
    (picks, _), logs = _find(fake_llm, script)
    assert len(picks) == 8
    assert not any(drama.is_judged(h) for h in picks)
    assert script.steered_prompts == []  # it cannot tell what is missing, so it does not guess
    assert any(l.startswith("Cross-window re-scoring failed") and l.endswith("keeping per-window scores") for l in logs)


# --- shorter videos, and the other flags --------------------------------------------------------------------------------------------------


def test_a_short_video_gets_the_same_treatment(fake_llm):
    script = Script(first=["calm"] * 4 + ["fight"] * 7)
    (picks, _), logs = _find(fake_llm, script, minutes=25)  # one window
    assert len(fake_llm.requests) == 2  # the window and the judging call
    assert not any(l.startswith(("Long video", "Window ")) for l in logs)  # no "part 1/1 of a longer video"
    assert "part 1/1" not in script.window_prompts[0]
    assert sum(is_fight(h) for h in picks) >= 6


def test_without_the_check_a_short_video_still_takes_the_single_call_path(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:02:00,000", "00:03:10,000", title="only")])
    picks, _ = windowing.find_highlights_windowed(
        transcript=make_transcript(20), video_info={}, num_clips=1, api_key="k", base_url=fake_llm.base_url, model="m"
    )
    assert len(fake_llm.requests) == 1 and picks[0]["title"] == "only"  # no judging call: the check is off


def test_asking_to_skip_the_re_scoring_does_not_skip_the_drama_judging(fake_llm):
    script = Script(first=["calm"] * 5 + ["fight"] * 6)
    _find(fake_llm, script, rerank=False)
    assert len(script.judge_prompts) == 1


def test_more_candidates_are_asked_for_when_the_check_is_on(fake_llm):
    on, off = Script(first=["fight"] * 3), Script(first=["fight"] * 3)
    _find(fake_llm, on)
    fake_llm.requests.clear()
    _find(fake_llm, off, drama_share=None, rerank=False)
    assert "return EXACTLY 15 segments" in on.window_prompts[0]  # 12 per window, plus the 3 upstream adds
    assert "return EXACTLY 11 segments" in off.window_prompts[0]  # 8 per window, as before


# --- the openings ---------------------------------------------------------------------------------------------------------------------------


def test_every_clip_opens_on_the_models_hook_not_on_a_long_run_up(fake_llm):
    (picks, _), _ = _find(fake_llm, Script(first=["calm"] * 3 + ["fight"] * 8))
    for h in picks:
        opened = windowing.parse_timestamp(h["model_start_time"]) - windowing._span(h)[0]
        assert 0 <= opened <= windowing.LEAD_IN_SECONDS + 5, (h["title"], opened)  # 5 s: one subtitle line
        assert 58 <= h["duration_seconds"] <= 120


def test_the_opening_strength_is_kept_with_each_pick(fake_llm):
    (picks, _), _ = _find(fake_llm, Script(first=["calm"] * 3 + ["fight"] * 8))
    assert all(h["opening_score"] == 2 for h in picks if is_fight(h))
    assert all(h["drama_why"] == "clash" for h in picks if is_fight(h))


# --- what the judge is told ----------------------------------------------------------------------------------------------------------------------


def test_the_judge_is_told_what_counts_and_shown_each_clips_opening_and_text(fake_llm):
    script = Script(first=["calm"] * 2 + ["fight"] * 3)
    _find(fake_llm, script)
    prompt = script.judge_prompts[0]

    for expected in ("DRAMA (0-10)", "OPENING (0-3)", "CATEGORY:", "Most candidates should score between 2 and 6", "STOP for this"):
        assert expected in prompt, expected
    from clipper.viral_prompt import CATEGORIES

    assert all(category in prompt for category in CATEGORIES)
    entries = re.findall(r"^\[\d+\] \d+s \| [^|]* \| title: .*? \| hook: .*? \| opening: (.*?) \| excerpt: (.*)$", prompt, re.M)
    assert len(entries) == 10  # five candidates in each of two windows
    for opening, excerpt in entries:
        assert opening and excerpt and len(opening) <= 240 and excerpt.startswith(opening[:20])
    assert prompt.rstrip().endswith("No prose and no markdown.")


def test_the_judges_reply_is_read_leniently(fake_llm):
    # a weak model leaves fields out, writes numbers as text, invents categories and puts quotes in 'why'
    items = [
        {"id": 0, "drama": "9", "opening": 3, "virality_score": 8, "category": "ARGUMENT", "why": 'she said "no"'},
        {"id": 1, "drama": 99, "opening": -4, "category": "made up"},
        {"id": 2, "virality_score": 6},
        {"id": 7, "drama": 8},
        {"drama": 8},
        "nonsense",
    ]
    parsed = windowing._parse_judgements(items, 3)
    assert parsed[0] == {"drama": 9, "opening": 3, "virality_score": 8, "category": "argument", "why": "she said 'no'"}
    assert parsed[1] == {"drama": 10, "opening": 0}  # clamped; the invented category is ignored
    assert parsed[2] == {"virality_score": 6}
    assert 7 not in parsed and len(parsed) == 3
    assert windowing._parse_judgements({"scores": items}, 3) == parsed  # also inside an object
    assert windowing._parse_judgements("junk", 3) == {}
