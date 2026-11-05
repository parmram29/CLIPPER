"""--niche, --trends and `clipper trends` through the command line."""

import json
import os
import time

import pytest

from clipper import cli

from test_cli_delivery import _ai_env, _session, cookies_file, fake_renderer  # noqa: F401 (fixtures)

REPLY = {
    "title": "Do not say that",
    "captions": [
        {"style": "tease", "text": "She went there and nobody moved"},
        {"style": "question", "text": "Would you have walked out?"},
        {"style": "hot take", "text": "She was right and you know it"},
    ],
    "hashtags": ["drama", "DramaAlert", "unlisted"],
    "keywords": ["reality tv fight"],
    "pinned_comment": "Team who?",
}


def _trends_file(tmp_path, text="Trending hashtags:\n#dramaalert #datingfails\n", age_days=1, name="trends.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    when = time.time() - age_days * 86400
    os.utime(path, (when, when))
    return path


def test_the_new_flags_exist_on_process_run_and_social():
    parser = cli.build_parser()
    for command in (["process", "d"], ["run", "https://youtu.be/x"], ["social", "d"]):
        args = parser.parse_args([*command, "--niche", "reality TV", "--trends", "t.txt"])
        assert (args.niche, args.trends) == ("reality TV", "t.txt")


# --- `clipper trends` -----------------------------------------------------------------------


def test_trends_init_creates_a_template_and_says_where_to_look(tmp_path, capsys):
    target = tmp_path / "my-trends.txt"
    assert cli.main(["trends", "--init", "--file", str(target)]) == 0
    out = capsys.readouterr().out
    assert target.is_file() and "Creative Center" in out and "how-to-use-trends" in out


def test_trends_init_refuses_to_overwrite(tmp_path, capsys):
    target = _trends_file(tmp_path, "#mine")
    assert cli.main(["trends", "--init", "--file", str(target)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "#mine"


def test_trends_status_when_there_is_no_file(tmp_path, capsys):
    assert cli.main(["trends", "--file", str(tmp_path / "none.txt")]) == 0
    out = capsys.readouterr().out
    assert "No trends available" in out and "clipper trends --refresh" in out


def test_trends_status_lists_the_hashtags_the_model_will_be_offered(tmp_path, capsys):
    assert cli.main(["trends", "--file", str(_trends_file(tmp_path, age_days=2))]) == 0
    out = capsys.readouterr().out
    assert "updated 2 day(s) ago" in out and "#dramaalert, #datingfails" in out


def test_trends_status_warns_when_stale(tmp_path, capsys):
    cli.main(["trends", "--file", str(_trends_file(tmp_path, age_days=30))])
    assert "STALE" in capsys.readouterr().out


# --- captions use them ----------------------------------------------------------------------


def test_social_passes_niche_and_trends_to_the_model_and_records_which_trend_tags_were_used(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    session = _session(tmp_path)
    trends = _trends_file(tmp_path)

    code = cli.main(["social", str(session), "--top", "1", "--niche", "reality TV drama", "--trends", str(trends)])
    out = capsys.readouterr().out
    assert code == 0

    prompt = fake_llm.requests[0]["body"]["messages"][-1]["content"]
    assert "#dramaalert #datingfails" in prompt and "Account niche: reality TV drama" in prompt

    copy = json.loads((session / "social" / "clip_000.json").read_text(encoding="utf-8"))
    assert copy["trend_tags_used"] == ["dramaalert"]  # "unlisted" was not in the user's file
    assert [v["style"] for v in copy["variants"]] == ["tease", "question", "hot take"]

    assert "Using trends from" in out
    assert "alt (question): Would you have walked out?" in out
    assert "pinned comment: Team who?" in out
    assert "uses trending tag(s): #dramaalert" in out


def test_process_uses_the_trends_file_too(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    session = _session(tmp_path)
    cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "1", "--trends", str(_trends_file(tmp_path)), "--niche", "dating shows"])
    prompt = fake_llm.requests[0]["body"]["messages"][-1]["content"]
    assert "#datingfails" in prompt and "dating shows" in prompt


def test_a_mistyped_trends_path_fails_before_anything_is_rendered(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    session = _session(tmp_path)
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--trends", str(tmp_path / "typo.txt")])
    assert code == 1
    assert "trends file not found" in capsys.readouterr().err
    assert fake_renderer == []  # the expensive step never ran


def test_no_social_never_reads_the_trends_file(tmp_path, cookies_file, fake_renderer, capsys):
    session = _session(tmp_path)
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--no-social", "--trends", str(tmp_path / "typo.txt")])
    assert code == 0 and len(fake_renderer) == 1
    assert "trends" not in capsys.readouterr().out.lower()


def test_the_default_trends_file_in_the_current_directory_is_picked_up(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    monkeypatch.chdir(tmp_path)
    _trends_file(tmp_path)  # ./trends.txt
    session = _session(tmp_path)
    cli.main(["social", str(session), "--top", "1"])
    assert "Using trends from trends.txt" in capsys.readouterr().out
    assert "#dramaalert" in fake_llm.requests[0]["body"]["messages"][-1]["content"]


def test_without_any_trends_file_it_says_so_and_still_writes_captions(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    monkeypatch.chdir(tmp_path)
    session = _session(tmp_path)
    assert cli.main(["social", str(session), "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "No trends available" in out and "clipper trends --refresh" in out
    assert "No current trend list was supplied" in fake_llm.requests[0]["body"]["messages"][-1]["content"]


def test_the_environment_can_supply_the_niche_and_trends_file(tmp_path, fake_llm, monkeypatch):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    monkeypatch.setenv("CLIPPER_NICHE", "true crime")
    monkeypatch.setenv("CLIPPER_TRENDS", str(_trends_file(tmp_path, "#truecrimetok")))
    session = _session(tmp_path)
    cli.main(["social", str(session), "--top", "1"])
    prompt = fake_llm.requests[0]["body"]["messages"][-1]["content"]
    assert "true crime" in prompt and "#truecrimetok" in prompt


def test_a_stale_trends_file_is_called_out_while_captions_are_written(tmp_path, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps(REPLY)
    session = _session(tmp_path)
    cli.main(["social", str(session), "--top", "1", "--trends", str(_trends_file(tmp_path, age_days=45))])
    assert "STALE" in capsys.readouterr().out
    assert "STALE" in fake_llm.requests[0]["body"]["messages"][-1]["content"]


def test_niche_and_trends_helpers():
    assert cli.resolve_niche("  drama  ") == "drama"
    assert cli.resolve_niche(None, {"CLIPPER_NICHE": "from env"}) == "from env"
    assert cli.resolve_niche("flag", {"CLIPPER_NICHE": "from env"}) == "flag"
    assert cli.resolve_niche("  ", {}) is None


def test_empty_values_from_a_fresh_env_file_mean_not_set(tmp_path, monkeypatch):
    # .env.example ships `CLIPPER_NICHE=` and `CLIPPER_TRENDS=` with nothing after the sign
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_niche(None, {"CLIPPER_NICHE": ""}) is None
    assert cli.load_trends_for_run(None, {"CLIPPER_TRENDS": ""}) is None  # default ./trends.txt, which is absent here
