"""The drama check: at least 6 of every 8 kept clips must be drama scenes; the rest may be anything.

From a real run: the first set of picks was mostly calm dating-show talk (one was a plea to subscribe),
and nothing checked that. These tests pin the rule, the ranking that enforces it, and the report.
"""

import json

import pytest

from clipper import cli, drama

from conftest import highlight


def pick(title, *, drama_score=None, opening=None, virality=7, category="drama", why=""):
    h = highlight("00:10:00,000", "00:11:10,000", title=title, score=virality)
    h["category"] = category
    if drama_score is not None:
        h["drama_score"] = drama_score
    if opening is not None:
        h["opening_score"] = opening
    if why:
        h["drama_why"] = why
    return h


# --- the bar: 6 of 8 -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total,share,needed",
    [(8, 0.75, 6), (3, 0.75, 3), (4, 0.75, 3), (5, 0.75, 4), (1, 0.75, 1), (10, 0.75, 8), (8, 0.5, 4), (8, 1.0, 8), (8, 0, 0), (0, 0.75, 0)],
)
def test_how_many_picks_must_be_drama_scenes(total, share, needed):
    assert drama.required_count(total, share) == needed


def test_the_default_bar_is_six_of_eight():
    assert drama.DEFAULT_SHARE == 0.75
    assert drama.required_count(8) == 6


@pytest.mark.parametrize(
    "text,expected",
    [("0.75", 0.75), ("75%", 0.75), ("75", 0.75), ("6/8", 0.75), ("1", 1.0), ("100%", 1.0), ("0", 0.0), ("0%", 0.0), ("1/2", 0.5), (" 50 % ", 0.5)],
)
def test_a_share_can_be_written_the_way_it_is_said(text, expected):
    assert drama.parse_share(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["lots", "9/8", "6/0", "150%", "-0.2", ""])
def test_a_nonsense_share_is_rejected_with_the_accepted_forms(text):
    with pytest.raises(ValueError, match="0.75|share"):
        drama.parse_share(text)


# --- what counts as a drama scene ---------------------------------------------------------------------------------


@pytest.mark.parametrize("score,expected", [(0, False), (3, False), (5, False), (6, True), (7, True), (10, True)])
def test_a_judged_pick_is_a_drama_scene_at_six_or_more(score, expected):
    assert drama.is_drama(pick("x", drama_score=score)) is expected


def test_the_judgement_overrules_the_models_own_label():
    # the model called calm talk "drama" (it did, in the real run); the separate judgement is what counts
    assert drama.is_drama(pick("sweet chat", drama_score=2, category="drama", virality=9)) is False
    assert drama.is_drama(pick("savage", drama_score=8, category="other", virality=4)) is True


@pytest.mark.parametrize("category", ["drama", "argument", "roast", "wtf", "funny", "emotional", "other", ""])
@pytest.mark.parametrize("virality", [1, 7, 10])
def test_a_pick_nobody_judged_is_never_counted_as_drama_whatever_the_model_called_it(category, virality):
    # the model's own label is the one that called calm talk "drama", so it is not evidence
    assert drama.is_drama(pick("x", category=category, virality=virality)) is False


def test_junk_scores_never_crash_the_rules():
    h = {"title": "x", "drama_score": "n/a", "opening_score": None, "virality_score": "high", "category": None}
    assert drama.drama_score(h) is None and drama.opening_score(h) is None
    assert drama.is_drama(h) is False and drama.is_judged(h) is False
    assert drama.rank_key(h) == (0, 0, 0, 0)


@pytest.mark.parametrize("opening,flat", [(0, True), (1, True), (2, False), (3, False), (None, False)])
def test_a_flat_opening_is_one_where_the_first_ten_seconds_are_set_up(opening, flat):
    assert drama.has_flat_opening(pick("x", drama_score=7, opening=opening)) is flat


# --- ranking --------------------------------------------------------------------------------------------------------------


def test_drama_scenes_outrank_calmer_picks_even_with_a_lower_generic_score():
    calm = pick("subscribe plea", drama_score=1, virality=9)
    fight = pick("you lied to my face", drama_score=8, virality=5)
    assert drama.rank_key(fight) > drama.rank_key(calm)


def test_among_drama_scenes_the_more_dramatic_then_the_stronger_opening_wins():
    a = pick("a", drama_score=7, opening=3, virality=5)
    b = pick("b", drama_score=9, opening=0, virality=5)
    c = pick("c", drama_score=7, opening=1, virality=9)
    assert sorted([a, b, c], key=drama.rank_key, reverse=True) == [b, a, c]


def test_picks_that_were_never_judged_rank_on_the_generic_score_alone():
    low, high = pick("low", virality=4), pick("high", virality=9)
    assert drama.rank_key(high) > drama.rank_key(low)


def test_top_picks_the_most_dramatic_clips_not_the_highest_generic_score():
    picks = [
        pick("calm but 9", drama_score=2, virality=9),
        pick("fight A", drama_score=8, virality=6),
        pick("fight B", drama_score=7, virality=7),
        pick("calm but 8", drama_score=3, virality=8),
    ]
    assert [picks[i]["title"] for i in cli.top_indices(picks, 2)] == ["fight A", "fight B"]


# --- the regression check ----------------------------------------------------------------------------------------------------


def _pool_shaped_like_the_failed_run(fights=6):
    """Candidates in which calm talk has the higher generic scores, as it did in the real run (the model
    even labelled it "drama"): the old ranking, by generic score alone, fills the top eight with calm talk."""
    calm = [pick(f"calm {i}", drama_score=2, opening=0, virality=8 - i % 2, category="drama") for i in range(6)]
    calm.append(pick("The 'subscribe' plea broke the vibe", drama_score=0, opening=0, virality=9, category="drama"))
    scenes = [pick(f"fight {i}", drama_score=7 + i % 3, opening=2, virality=6, category="argument") for i in range(fights)]
    return calm + scenes


def test_ranking_by_the_generic_score_alone_would_fail_the_bar_on_that_pool():
    pool = _pool_shaped_like_the_failed_run()
    old_top8 = sorted(pool, key=drama.virality, reverse=True)[:8]
    report = drama.check(old_top8, drama.DEFAULT_SHARE)
    assert report.drama_count < report.required and not report.passed  # the regression this guards against
    assert "The 'subscribe' plea broke the vibe" in [h["title"] for h in old_top8]


def test_ranking_by_drama_passes_the_bar_on_the_same_pool_and_leaves_the_plea_out():
    pool = _pool_shaped_like_the_failed_run()
    top8 = [pool[i] for i in cli.top_indices(pool, 8)]
    report = drama.check(top8, drama.DEFAULT_SHARE)
    assert report.passed and report.drama_count >= 6
    assert "The 'subscribe' plea broke the vibe" not in [h["title"] for h in top8]
    assert [h["title"] for h in top8[:5]][0].startswith("fight")  # the drama comes first


def test_if_the_video_really_has_too_little_drama_the_check_fails_honestly_instead_of_passing():
    pool = _pool_shaped_like_the_failed_run(fights=5)  # only five drama scenes exist to choose from
    top8 = [pool[i] for i in cli.top_indices(pool, 8)]
    report = drama.check(top8)
    assert report.drama_count == 5 and not report.passed  # no ranking can conjure a sixth
    assert all(drama.is_drama(h) for h in top8[:5])  # but all five come first


def test_six_of_eight_passes_and_five_of_eight_fails():
    six = [pick(f"d{i}", drama_score=8) for i in range(6)] + [pick(f"c{i}", drama_score=2) for i in range(2)]
    five = [pick(f"d{i}", drama_score=8) for i in range(5)] + [pick(f"c{i}", drama_score=2) for i in range(3)]
    assert drama.check(six).passed and drama.check(six).required == 6
    assert not drama.check(five).passed and drama.check(five).drama_count == 5


def test_the_others_may_be_anything_unless_the_bar_is_raised():
    picks = [pick(f"d{i}", drama_score=8) for i in range(6)] + [pick("sweet", drama_score=0), pick("plea", drama_score=0)]
    assert drama.check(picks, 0.75).passed
    assert not drama.check(picks, 1.0).passed  # "unless otherwise noted": all drama
    assert drama.check(picks, 0.5).passed
    assert drama.check(picks, 0).passed


def test_there_is_nothing_to_pass_with_no_picks():
    report = drama.check([])
    assert not report.passed and "no picks" in drama.format_report(report)


def test_a_bar_of_exactly_the_threshold_counts():
    picks = [pick(f"d{i}", drama_score=6) for i in range(6)] + [pick(f"c{i}", drama_score=5) for i in range(2)]
    assert drama.check(picks).passed  # 6 is drama, 5 is not


# --- what the report says ----------------------------------------------------------------------------------------------------------


def test_the_report_names_the_verdict_each_pick_and_why():
    picks = [pick("Girl, do you want to get slapped?", drama_score=9, opening=3, category="argument", why="threat")] * 6 + [
        pick("The 'subscribe' plea broke the vibe", drama_score=0, opening=0, category="other"),
        pick("I like dates on the beach", drama_score=1, opening=1, category="other"),
    ]
    text = drama.format_report(drama.check(picks))
    assert text.startswith("Drama check: 6 of 8 picks are drama scenes (at least 6 needed, 75%): PASS")
    assert "Girl, do you want to get slapped?" in text and "(threat)" in text
    assert text.count("<- not a drama scene") == 2
    assert "Flat opening" in text and "pick(s) 7, 8" in text
    assert "never scored" not in text


def test_a_failing_report_says_what_to_do_about_it():
    picks = [pick(f"c{i}", drama_score=2) for i in range(8)]
    text = drama.format_report(drama.check(picks))
    assert "0 of 8" in text and "FAIL" in text
    assert "--direction" in text and "--drama-share" in text


def test_picks_nobody_judged_are_called_out_and_do_not_count():
    report = drama.check([pick("a", category="drama", virality=10), pick("b", drama_score=8)])
    assert report.drama_count == 1 and report.judged == 1
    text = drama.format_report(report)
    assert "1 pick(s) were never scored" in text and "--rejudge" in text


def test_a_session_nobody_judged_fails_the_check_until_it_is_scored(tmp_path, capsys):
    picks = [pick(f"p{i}", category="drama", virality=9) for i in range(8)]  # all labelled drama by the model
    assert cli.main(["check", str(_session(tmp_path, picks))]) == 1
    out = capsys.readouterr().out
    assert "0 of 8 picks are drama scenes" in out and "8 pick(s) were never scored" in out


# --- the check command ------------------------------------------------------------------------------------------------------------------


def _session(tmp_path, picks):
    session = tmp_path / "s1"
    session.mkdir()
    (session / "session_data.json").write_text(
        json.dumps({"url": "https://youtu.be/abc", "output_language_name": "English", "highlights": picks})
    )
    return session


def test_the_check_command_passes_with_exit_status_0(tmp_path, capsys):
    picks = [pick(f"d{i}", drama_score=8) for i in range(6)] + [pick("c1", drama_score=2), pick("c2", drama_score=3)]
    assert cli.main(["check", str(_session(tmp_path, picks))]) == 0
    assert "PASS" in capsys.readouterr().out


def test_the_check_command_fails_with_exit_status_1(tmp_path, capsys):
    picks = [pick(f"d{i}", drama_score=8) for i in range(4)] + [pick(f"c{i}", drama_score=2) for i in range(4)]
    assert cli.main(["check", str(_session(tmp_path, picks))]) == 1
    assert "4 of 8" in capsys.readouterr().out


def test_the_bar_can_be_changed_on_the_command_line_or_in_the_environment(tmp_path, monkeypatch):
    picks = [pick(f"d{i}", drama_score=8) for i in range(4)] + [pick(f"c{i}", drama_score=2) for i in range(4)]
    session = str(_session(tmp_path, picks))
    assert cli.main(["check", session, "--drama-share", "0.5"]) == 0
    assert cli.main(["check", session, "--drama-share", "4/8"]) == 0
    assert cli.main(["check", session]) == 1
    monkeypatch.setenv("CLIPPER_DRAMA_SHARE", "50%")
    assert cli.main(["check", session]) == 0


def test_a_bad_share_is_reported_not_a_traceback(tmp_path, capsys):
    assert cli.main(["check", str(_session(tmp_path, [pick("a", drama_score=8)])), "--drama-share", "lots"]) == 1
    assert "cannot read" in capsys.readouterr().err


# --- who gets the check by default ---------------------------------------------------------------------------------------------------------


def _share(*argv, env=None):
    args = cli.build_parser().parse_args(["find", "https://youtu.be/x", *argv])
    return cli.resolve_drama_share(args, env or {})


def test_the_viral_profile_has_the_check_on_by_default_at_six_of_eight():
    assert _share() == 0.75


def test_other_profiles_and_your_own_prompt_do_not_get_it_unless_asked(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_text("{transcript} {num_clips}")
    assert _share("--profile", "podcast") is None
    assert _share("--prompt-file", str(prompt)) is None
    assert _share("--profile", "podcast", "--drama-share", "0.5") == 0.5


def test_it_can_be_set_or_switched_off_per_run_or_for_good():
    assert _share("--drama-share", "6/8") == 0.75
    assert _share("--drama-share", "1.0") == 1.0
    assert _share("--drama-share", "0") is None
    assert _share(env={"CLIPPER_DRAMA_SHARE": "0.5"}) == 0.5
    assert _share(env={"CLIPPER_DRAMA_SHARE": "0"}) is None
    assert _share("--drama-share", "0.9", env={"CLIPPER_DRAMA_SHARE": "0.5"}) == 0.9  # the flag wins
    assert _share(env={"CLIPPER_DRAMA_SHARE": ""}) == 0.75  # an empty line in .env means "not set"


def test_the_highlight_list_shows_the_drama_score_when_there_is_one():
    text = cli.format_highlights([pick("A", drama_score=8), pick("B")])
    assert "[7/10 drama 8]" in text and text.count("drama") == 1
