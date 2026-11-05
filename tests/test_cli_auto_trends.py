"""Trends researched automatically, through the command line."""

import json
import os
import sys
import time
import types

import pytest

from clipper import cli, trend_research

from test_cli_delivery import _ai_env, _session, cookies_file, fake_renderer  # noqa: F401 (fixtures)
from test_cli_trends import REPLY as CAPTION_REPLY
from test_trend_research import PAGE, RESEARCH_JSON, SEARCH_INFO


def _is_research(body):
    return "Use web search" in body["messages"][-1]["content"]


def _two_stage(server, research=RESEARCH_JSON, sources=SEARCH_INFO):
    server.extra_response = dict(sources) if sources else {}
    server.reply_fn = lambda body: json.dumps(research if _is_research(body) else CAPTION_REPLY)


def _researched(server):
    return [r for r in server.requests if _is_research(r["body"])]


def _captioned(server):
    return [r for r in server.requests if not _is_research(r["body"])]


@pytest.fixture
def auto_on(monkeypatch, fake_llm, tmp_path):
    """A session, a key, a fake endpoint that answers both research and captions, and the
    automatic research switched on (the test suite switches it off by default)."""
    _ai_env(monkeypatch, fake_llm)
    monkeypatch.setenv("CLIPPER_AUTO_TRENDS", "1")
    monkeypatch.chdir(tmp_path)  # no ./trends.txt here
    _two_stage(fake_llm)
    return _session(tmp_path)


# --- the default behaviour -------------------------------------------------------------------------------


def test_trends_are_researched_automatically_when_you_have_not_supplied_any(auto_on, fake_llm, capsys):
    session = auto_on
    assert cli.main(["social", str(session), "--top", "1", "--niche", "reality TV drama"]) == 0
    out = capsys.readouterr().out

    research, caption = _researched(fake_llm), _captioned(fake_llm)
    assert len(research) == 1 and len(caption) == 1
    assert research[0]["body"]["enable_search"] is True  # the research call asked for web search
    assert "enable_search" not in caption[0]["body"]  # the caption call did not

    prompt = caption[0]["body"]["messages"][-1]["content"]
    assert "researched automatically from the web" in prompt and "#dramaalert #datingfails" in prompt
    assert "madeup" not in prompt  # not backed by a returned source, so it never reached the captions

    assert "Researching current trends" in out and "Using trends researched from the web" in out
    copy = json.loads((session / "social" / "clip_000.json").read_text(encoding="utf-8"))
    assert copy["trend_tags_used"] == ["dramaalert"]


def test_it_is_on_by_default(monkeypatch, fake_llm, tmp_path):
    _ai_env(monkeypatch, fake_llm)
    monkeypatch.delenv("CLIPPER_AUTO_TRENDS")  # nothing set at all
    monkeypatch.chdir(tmp_path)
    _two_stage(fake_llm)
    cli.main(["social", str(_session(tmp_path)), "--top", "1"])
    assert len(_researched(fake_llm)) == 1


def test_the_result_is_cached_so_later_runs_cost_nothing_extra(auto_on, fake_llm):
    cli.main(["social", str(auto_on), "--top", "1"])
    cli.main(["social", str(auto_on), "--top", "1", "--force"])
    cli.main(["social", str(auto_on), "--top", "2", "--force"])
    assert len(_researched(fake_llm)) == 1 and len(_captioned(fake_llm)) == 4  # 1 + 1 + 2 clips


def test_refresh_trends_researches_again_now(auto_on, fake_llm):
    cli.main(["social", str(auto_on), "--top", "1"])
    cli.main(["social", str(auto_on), "--top", "1", "--force", "--refresh-trends"])
    assert len(_researched(fake_llm)) == 2


def test_old_research_is_refreshed_by_itself(auto_on, fake_llm, monkeypatch):
    cli.main(["social", str(auto_on), "--top", "1"])
    later = time.time() + (trend_research.AUTO_REFRESH_DAYS + 1) * 86400
    monkeypatch.setattr(trend_research.time, "time", lambda: later)
    cli.main(["social", str(auto_on), "--top", "1", "--force"])
    assert len(_researched(fake_llm)) == 2


@pytest.mark.parametrize("value", ["0", "false", "No", "OFF", " 0 "])
def test_it_can_be_switched_off_from_the_environment(value, auto_on, fake_llm, monkeypatch):
    monkeypatch.setenv("CLIPPER_AUTO_TRENDS", value)
    cli.main(["social", str(auto_on), "--top", "1"])
    assert _researched(fake_llm) == []
    assert "No current trend list was supplied" in _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything-else", ""])
def test_auto_trends_enabled_parsing(value):
    assert cli.auto_trends_enabled({"CLIPPER_AUTO_TRENDS": value}) is True


def test_the_flag_switches_it_off(auto_on, fake_llm):
    cli.main(["social", str(auto_on), "--top", "1", "--no-auto-trends"])
    assert _researched(fake_llm) == []


def test_without_an_api_key_it_quietly_does_nothing(monkeypatch, fake_llm, tmp_path, capsys):
    monkeypatch.setenv("CLIPPER_AUTO_TRENDS", "1")
    monkeypatch.chdir(tmp_path)
    assert cli.load_trends_for_run(None) is None
    assert fake_llm.requests == []
    assert "No trends available" in capsys.readouterr().out


# --- priority: your own file wins ---------------------------------------------------------------------------


def test_a_file_you_name_beats_automatic_research(auto_on, fake_llm, tmp_path):
    mine = tmp_path / "mine.txt"
    mine.write_text("#myowntag", encoding="utf-8")
    cli.main(["social", str(auto_on), "--top", "1", "--trends", str(mine)])
    assert _researched(fake_llm) == []
    assert "#myowntag" in _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]


def test_a_filled_in_trends_txt_beats_automatic_research(auto_on, fake_llm, tmp_path):
    (tmp_path / "trends.txt").write_text("#fromfile", encoding="utf-8")
    cli.main(["social", str(auto_on), "--top", "1"])
    assert _researched(fake_llm) == []
    assert "#fromfile" in _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]


def test_an_unedited_template_is_ignored_and_does_not_block_research(auto_on, fake_llm, tmp_path, capsys):
    cli.main(["trends", "--init", "--file", str(tmp_path / "trends.txt")])  # created, never edited
    capsys.readouterr()
    cli.main(["social", str(auto_on), "--top", "1"])
    out = capsys.readouterr().out
    assert "still the unedited template" in out
    assert len(_researched(fake_llm)) == 1
    prompt = _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]
    assert "examplehashtag" not in prompt and "#dramaalert" in prompt  # the placeholders never reach the captions


# --- what it researches ----------------------------------------------------------------------------------------


def test_niche_and_region_shape_the_research(auto_on, fake_llm):
    cli.main(["social", str(auto_on), "--top", "1", "--niche", "reality TV", "--region", "the UK"])
    prompt = _researched(fake_llm)[0]["body"]["messages"][-1]["content"]
    assert "in the niche: reality TV" in prompt and ", for the UK" in prompt


def test_the_region_can_come_from_the_environment(auto_on, fake_llm, monkeypatch):
    monkeypatch.setenv("CLIPPER_REGION", "Canada")
    cli.main(["social", str(auto_on), "--top", "1"])
    assert ", for Canada" in _researched(fake_llm)[0]["body"]["messages"][-1]["content"]


def test_a_separate_search_model_is_used_for_research_only(auto_on, fake_llm, monkeypatch):
    monkeypatch.setenv("CLIPPER_SEARCH_MODEL", "qwen-plus")
    cli.main(["social", str(auto_on), "--top", "1"])
    assert _researched(fake_llm)[0]["body"]["model"] == "qwen-plus"
    assert _captioned(fake_llm)[0]["body"]["model"] == "qwen-flash"


def test_search_ai_config(monkeypatch):
    assert cli.search_ai_config({}) is None  # no key
    base = {"CLIPPER_API_KEY": "k", "CLIPPER_BASE_URL": "https://x.test/v1", "CLIPPER_MODEL": "chat-model"}
    assert cli.search_ai_config(base)["model"] == "chat-model"
    assert cli.search_ai_config({**base, "CLIPPER_SEARCH_MODEL": " search-model "})["model"] == "search-model"


def test_region_resolution():
    assert cli.resolve_region(" UK ", {}) == "UK"
    assert cli.resolve_region(None, {"CLIPPER_REGION": "Canada"}) == "Canada"
    assert cli.resolve_region("flag", {"CLIPPER_REGION": "env"}) == "flag"
    assert cli.resolve_region("  ", {"CLIPPER_REGION": ""}) is None


# --- when search is not available ----------------------------------------------------------------------------------


def test_captions_are_still_written_when_search_is_refused(auto_on, fake_llm, capsys):
    fake_llm.reject_keys = {"enable_search"}
    assert cli.main(["social", str(auto_on), "--top", "1"]) == 0
    out = capsys.readouterr().out
    assert "Could not research trends" in out and "CLIPPER_SEARCH_MODEL" in out
    assert "No current trend list was supplied" in _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]
    assert (auto_on / "social" / "clip_000.json").is_file()


def test_unverified_research_is_labelled_everywhere(auto_on, fake_llm, capsys):
    _two_stage(fake_llm, sources=None)  # the provider returns no sources
    cli.main(["social", str(auto_on), "--top", "1"])
    out = capsys.readouterr().out
    assert "UNVERIFIED" in out
    assert "UNVERIFIED" in _captioned(fake_llm)[0]["body"]["messages"][-1]["content"]


# --- clipper trends --------------------------------------------------------------------------------------------------


def test_trends_refresh_shows_what_was_found_and_how_well_it_checked_out(auto_on, fake_llm, capsys):
    assert cli.main(["trends", "--refresh", "--niche", "reality TV"]) == 0
    out = capsys.readouterr().out
    assert "hashtags: #dramaalert #datingfails" in out
    assert "topic: The reality show finale" in out and "format: POV:" in out
    assert "sources returned by the search: 2" in out and "dropped as unverifiable: 3" in out
    assert PAGE in out
    assert "UNVERIFIED" not in out


def test_trends_refresh_warns_when_nothing_could_be_checked(auto_on, fake_llm, capsys):
    _two_stage(fake_llm, sources=None)
    assert cli.main(["trends", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "returned NO sources" in out and "UNVERIFIED" in out


def test_trends_without_refresh_reads_the_cache_and_makes_no_call(auto_on, fake_llm, capsys):
    cli.main(["trends", "--refresh"])
    calls = len(fake_llm.requests)
    capsys.readouterr()
    assert cli.main(["trends"]) == 0
    assert len(fake_llm.requests) == calls
    assert "#dramaalert" in capsys.readouterr().out


def test_trends_with_nothing_cached_says_how_to_get_some(auto_on, fake_llm, capsys):
    assert cli.main(["trends"]) == 0
    assert "No trends available" in capsys.readouterr().out and fake_llm.requests == []


def test_trends_refresh_needs_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["trends", "--refresh"]) == 1
    assert "No API key configured" in capsys.readouterr().err


def test_trends_refresh_fails_loudly_when_search_is_refused(auto_on, fake_llm, capsys):
    fake_llm.reject_keys = {"enable_search"}
    assert cli.main(["trends", "--refresh"]) == 1
    out = capsys.readouterr().out
    assert "Could not research trends" in out and "No trends available" in out


def test_a_trends_file_you_wrote_is_shown_instead_and_takes_priority(auto_on, fake_llm, tmp_path, capsys):
    mine = tmp_path / "mine.txt"
    mine.write_text("#myowntag", encoding="utf-8")
    assert cli.main(["trends", "--file", str(mine), "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "#myowntag" in out and "takes priority" in out
    assert fake_llm.requests == []  # nothing researched


# --- process: research happens before the expensive render -----------------------------------------------------------------


def test_process_researches_trends_before_rendering_and_still_renders_if_search_fails(auto_on, fake_llm, cookies_file, monkeypatch):
    seen = {}

    def fake_render(**kwargs):
        seen["requests_before_render"] = len(fake_llm.requests)
        return {"results": []}

    stub = types.ModuleType("clipper.clip_processor")
    stub.process_selected_highlights = fake_render
    monkeypatch.setitem(sys.modules, "clipper.clip_processor", stub)

    assert cli.main(["process", str(auto_on), "--cookies", str(cookies_file), "--top", "1"]) == 0
    assert seen["requests_before_render"] == 1  # the research call came first

    # and a refused search does not stop the render
    fake_llm.reject_keys = {"enable_search"}
    seen.clear()
    cli.main(["process", str(auto_on), "--cookies", str(cookies_file), "--top", "1", "--refresh-trends"])
    assert "requests_before_render" in seen


def test_the_new_flags_exist_where_they_should():
    parser = cli.build_parser()
    for command in (["process", "d"], ["run", "https://youtu.be/x"], ["social", "d"]):
        args = parser.parse_args([*command, "--region", "UK", "--no-auto-trends", "--refresh-trends"])
        assert (args.region, args.no_auto_trends, args.refresh_trends) == ("UK", True, True)
    args = parser.parse_args(["trends", "--refresh", "--niche", "n", "--region", "r"])
    assert (args.refresh, args.niche, args.region) == (True, "n", "r")
