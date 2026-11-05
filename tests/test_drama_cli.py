"""The drama check through the command line: `find` prints it, `check` repeats it, `--rejudge` scores picks you already have."""

import json
from pathlib import Path

import pytest

from clipper import cli, drama, session as session_mod

from conftest import highlight, write_srt
from test_cli_delivery import _ai_env, cookies_file  # noqa: F401 (fixture)
from test_drama_pipeline import Script, is_fight


@pytest.fixture
def long_video(monkeypatch):
    def fake_subtitle_only(url, subtitle_language, temp_dir, cookies_path, log=None):
        srt = Path(temp_dir) / f"source.{subtitle_language}.srt"
        write_srt(srt, 100 * 60)  # four 30-minute windows
        return str(srt), {"title": "Reunion", "channel": "Chan", "description": "d"}

    monkeypatch.setattr(session_mod, "download_subtitle_only", fake_subtitle_only)
    monkeypatch.setattr(session_mod, "download_caption_words", lambda **kw: None)


def _saved(tmp_path):
    (data_file,) = (tmp_path / "out" / "sessions").glob("*/session_data.json")
    return json.loads(data_file.read_text(encoding="utf-8"))


# --- find ------------------------------------------------------------------------------------------------------------------------


def test_find_judges_the_drama_by_default_and_prints_the_check(tmp_path, fake_llm, cookies_file, monkeypatch, capsys, long_video):
    _ai_env(monkeypatch, fake_llm)
    script = fake_llm.reply_fn = Script(first=["calm"] * 2 + ["fight"] * 4)
    code = cli.main(["find", "https://youtu.be/x", "--clips", "8", "--cookies", str(cookies_file), "--out", str(tmp_path / "out")])
    assert code == 0

    out = capsys.readouterr().out
    assert "Highlights:" in out
    assert "Drama check: 8 of 8 picks are drama scenes (at least 6 needed, 75%): PASS" in out
    assert " drama 8]" in out  # the list shows the drama score next to the generic one
    assert len(script.judge_prompts) == 1

    picks = _saved(tmp_path)["highlights"]
    assert all(is_fight(h) for h in picks)
    assert all(h["drama_score"] == 8 and h["opening_score"] == 2 and h["category"] == "argument" for h in picks)
    assert all(h["category_first_pass"] == "drama" for h in picks)  # the model's own label is kept for comparison


def test_a_failing_check_does_not_stop_find_but_says_so(tmp_path, fake_llm, cookies_file, monkeypatch, capsys, long_video):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = Script(first=["calm"] * 5)  # a video with no drama in it at all
    code = cli.main(["find", "https://youtu.be/x", "--clips", "8", "--cookies", str(cookies_file), "--out", str(tmp_path / "out"),
                     "--direction", "only the best parts"])  # a direction of your own: no second look
    out = capsys.readouterr().out
    assert code == 0 and "0 of 8 picks are drama scenes" in out and "FAIL" in out
    assert "Too few drama scenes" in out and "--drama-share" in out


def test_the_bar_can_be_changed_per_run(tmp_path, fake_llm, cookies_file, monkeypatch, capsys, long_video):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = Script(first=["calm"] * 3 + ["fight"] * 3)  # 12 fights, 12 calm: 8 picks are all fights
    cli.main(["find", "https://youtu.be/x", "--clips", "8", "--cookies", str(cookies_file), "--out", str(tmp_path / "out"),
              "--drama-share", "1.0"])
    assert "(at least 8 needed, 100%): PASS" in capsys.readouterr().out


def test_switched_off_the_plain_pipeline_runs_and_prints_no_check(tmp_path, fake_llm, cookies_file, monkeypatch, capsys, long_video):
    _ai_env(monkeypatch, fake_llm)
    script = fake_llm.reply_fn = Script(first=["calm"] * 3)
    cli.main(["find", "https://youtu.be/x", "--clips", "3", "--cookies", str(cookies_file), "--out", str(tmp_path / "out"),
              "--drama-share", "0", "--no-rerank"])
    out = capsys.readouterr().out
    assert "Drama check" not in out and script.judge_prompts == []
    assert not any("drama_score" in h for h in _saved(tmp_path)["highlights"])


def test_the_podcast_profile_does_not_get_the_check(tmp_path, fake_llm, cookies_file, monkeypatch, capsys, long_video):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = Script(first=["calm"] * 3)
    cli.main(["find", "https://youtu.be/x", "--clips", "3", "--cookies", str(cookies_file), "--out", str(tmp_path / "out"),
              "--profile", "podcast", "--no-rerank"])
    assert "Drama check" not in capsys.readouterr().out


# --- check --rejudge ------------------------------------------------------------------------------------------------------------------


def _saved_session(tmp_path, titles, *, keep_subtitles=True):
    """A session as `find` left it before the drama judgement existed: picks with the model's own labels."""
    session = tmp_path / "s1"
    (session / "_temp").mkdir(parents=True)
    if keep_subtitles:
        write_srt(session / "_temp" / "source.en.srt", 40 * 60)
    picks = []
    for i, title in enumerate(titles):
        h = highlight(f"00:{2 + i * 4:02d}:00,000", f"00:{2 + i * 4:02d}:10,000", title=title, score=8, hook="h")
        h.update(category="drama", duration_seconds=70.0, transcript_text=f"words spoken in {title}")
        picks.append(h)
    (session / "session_data.json").write_text(json.dumps({"url": "https://youtu.be/abc", "highlights": picks}))
    return session


def test_rejudging_scores_the_picks_you_already_have_and_saves_the_scores(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    script = fake_llm.reply_fn = Script(first=[])
    session = _saved_session(tmp_path, [f"fight {i}" for i in range(6)] + ["calm a", "plea b"])

    # before: all eight carry the model's own label ("drama", 8/10), which is no evidence, so it cannot pass
    assert cli.main(["check", str(session)]) == 1
    assert "never scored" in capsys.readouterr().out and script.judge_prompts == []

    assert cli.main(["check", str(session), "--rejudge"]) == 0
    out = capsys.readouterr().out
    assert "6 of 8 picks are drama scenes" in out and "PASS" in out and "<- not a drama scene" in out
    assert len(script.judge_prompts) == 1
    # the subtitle file the session kept was used for the text, not the short copy saved with each pick
    assert "words at" in script.judge_prompts[0] and "words spoken in" not in script.judge_prompts[0]

    saved = json.loads((session / "session_data.json").read_text())["highlights"]
    assert [h["drama_score"] for h in saved] == [8] * 6 + [2, 0]

    # the scores are saved, so the check works offline from now on and --top picks the fights first
    assert cli.main(["check", str(session)]) == 0 and len(script.judge_prompts) == 1
    picks = [dict(h) for h in saved]
    assert all(is_fight(picks[i]) for i in cli.top_indices(picks, 6))


def test_a_session_whose_subtitles_were_not_kept_is_judged_from_the_text_saved_with_each_pick(tmp_path, fake_llm, monkeypatch):
    _ai_env(monkeypatch, fake_llm)
    script = fake_llm.reply_fn = Script(first=[])
    session = _saved_session(tmp_path, ["fight 0", "calm 1"], keep_subtitles=False)
    cli.main(["check", str(session), "--rejudge"])
    prompt = script.judge_prompts[0]
    assert "words spoken in fight 0" in prompt and "words spoken in calm 1" in prompt


def test_if_the_judge_cannot_be_reached_the_check_still_reports_and_changes_nothing(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = Script(first=[], judge="garbage")
    session = _saved_session(tmp_path, [f"fight {i}" for i in range(8)])
    before = (session / "session_data.json").read_text()
    code = cli.main(["check", str(session), "--rejudge"])
    out = capsys.readouterr().out
    assert "never scored" in out
    assert json.loads((session / "session_data.json").read_text())["highlights"] == json.loads(before)["highlights"]
    assert code == 1  # it could not confirm anything, and an unconfirmed check does not pass
