"""`clipper trends --diagnose`, and remembering why a search request form was refused."""

from datetime import date

import pytest

from clipper import cli, llm, trend_research as tr

from test_trend_research import EMPTY, PAGE, SEARCH_INFO, _ai, _empty_run, _research_server

TODAY = date(2026, 11, 5)


def _diagnose(server, api="dashscope", **kwargs):
    kwargs.setdefault("niche", "reality TV drama")
    kwargs.setdefault("region", "the US")
    return "\n".join(tr.diagnose(_ai(server), today=TODAY, api=api, **kwargs))


# --- each form is sent on its own -------------------------------------------------------------------


def test_each_native_form_is_tried_on_its_own_and_reported(fake_llm):
    _research_server(fake_llm)
    fake_llm.reply = "1. #dramaalert (example.com/trends/week)"
    text = _diagnose(fake_llm)

    assert "[agent search] accepted. Sources returned: 2" in text
    assert "[enable_search + options] accepted. Sources returned: 2" in text
    assert "[enable_search only] accepted. Sources returned: 2" in text
    assert len(fake_llm.requests) == 3  # no fallback between them: one call per form

    params = [r["body"]["parameters"] for r in fake_llm.requests]
    assert params[0]["search_options"]["search_strategy"] == "agent"
    assert "search_options" in params[1] and "search_strategy" not in params[1]["search_options"]
    assert "search_options" not in params[2] and params[2]["enable_search"] is True


def test_a_refused_form_is_reported_with_the_reason_and_the_others_still_run(fake_llm):
    _research_server(fake_llm)
    fake_llm.reject_strategy = True
    text = _diagnose(fake_llm)
    assert "[agent search] REFUSED:" in text and "search_strategy" in text  # the endpoint's own message
    assert "[enable_search + options] accepted" in text and "[enable_search only] accepted" in text


def test_when_every_form_is_refused_it_says_so_three_times(fake_llm):
    fake_llm.reject_keys = {"enable_search"}
    results = _diagnose(fake_llm).split("How to read this")[0]  # the guide below also says "REFUSED:"
    assert results.count("REFUSED:") == 3 and "accepted" not in results


def test_the_sources_are_listed_with_their_titles_and_addresses(fake_llm):
    _research_server(fake_llm)
    text = _diagnose(fake_llm)
    assert f"- Trending on TikTok this week | {PAGE}" in text
    assert "- More hashtags | https://other.example/list" in text


def test_no_sources_is_shown_as_zero(fake_llm):
    _research_server(fake_llm, sources=None)
    assert "Sources returned: 0" in _diagnose(fake_llm)


def test_the_models_answer_is_shown_but_cut_short(fake_llm):
    _research_server(fake_llm)
    fake_llm.reply = "word " * 1000
    answer_lines = [line for line in _diagnose(fake_llm).splitlines() if line.strip().startswith("answer:")]
    assert len(answer_lines) == 3 and all(len(line) <= 4 + len("answer: ") + 600 for line in answer_lines)


def test_the_compatible_route_has_its_own_two_forms(fake_llm):
    _research_server(fake_llm)
    text = _diagnose(fake_llm, api="compat")
    assert "route: compat" in text and "[enable_search + options] accepted" in text and "[enable_search only] accepted" in text
    assert "agent search" not in text
    assert len(fake_llm.requests) == 2
    assert all(not r["path"].endswith("/generation") for r in fake_llm.requests)


def test_the_question_is_a_plain_one_about_this_niche_region_and_date(fake_llm):
    _research_server(fake_llm)
    text = _diagnose(fake_llm)
    question = next(line for line in text.splitlines() if line.startswith("Question:"))
    assert "this week (today is 2026-11-05)" in question
    assert "the US" in question and "reality TV drama" in question
    assert "JSON" not in question  # free-form on purpose: it shows what the model says when not boxed in
    sent = fake_llm.requests[0]["body"]["input"]["messages"][-1]["content"]
    assert question == f"Question: {sent}"


def test_the_reading_guide_is_always_there(fake_llm):
    fake_llm.reject_keys = {"enable_search"}
    text = _diagnose(fake_llm)
    for phrase in ("How to read this:", "Every form REFUSED", "0 sources", "another language", "too strict"):
        assert phrase in text


def test_a_diagnosis_never_writes_to_the_cache(fake_llm, tmp_path):
    _research_server(fake_llm)
    _diagnose(fake_llm)
    cache_dir = tr.cache_path("reality TV drama", "the US").parent
    assert not cache_dir.exists() or not any(cache_dir.iterdir())


def test_an_unexpected_error_is_reported_and_the_next_form_still_runs(fake_llm, monkeypatch):
    real = llm.chat_with_search
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("something odd")
        return real(*args, **kwargs)

    monkeypatch.setattr(llm, "chat_with_search", flaky)
    _research_server(fake_llm)
    text = _diagnose(fake_llm)
    assert "[agent search] FAILED: ValueError: something odd" in text
    assert "[enable_search + options] accepted" in text


# --- remembering why a form was refused ---------------------------------------------------------------------


def test_the_reason_an_earlier_native_form_was_refused_is_kept(fake_llm):
    _research_server(fake_llm)
    fake_llm.reject_strategy = True
    info = {}
    llm.chat_with_search(_ai(fake_llm), "x", api="dashscope", info=info)
    assert info["request"] == "native API: enable_search + options"
    assert len(info["refused"]) == 1
    assert info["refused"][0].startswith("agent search: HTTP 400") and "search_strategy" in info["refused"][0]


def test_the_reason_an_earlier_compatible_form_was_refused_is_kept(fake_llm):
    _research_server(fake_llm)
    fake_llm.reject_keys = {"search_options"}
    info = {}
    llm.chat_with_search(_ai(fake_llm), "x", info=info)
    assert info["request"] == "enable_search only"
    assert info["refused"][0].startswith("enable_search + options:") and "search_options" in info["refused"][0]


def test_nothing_is_recorded_as_refused_when_the_first_form_works(fake_llm):
    _research_server(fake_llm)
    info = {}
    llm.chat_with_search(_ai(fake_llm), "x", api="dashscope", info=info)
    assert info["refused"] == []


def test_attempts_restricts_the_forms_tried(fake_llm):
    fake_llm.reject_strategy = True
    info = {}
    agent_only = llm.search_forms("dashscope")[:1]
    with pytest.raises(llm.SearchUnavailable):
        llm.chat_with_search(_ai(fake_llm), "x", api="dashscope", attempts=agent_only, info=info)
    assert len(fake_llm.requests) == 1  # it did not fall back to a simpler form
    assert info["refused"] and info["refused"][0].startswith("agent search:")


def test_an_unreachable_endpoint_is_recorded_with_the_kind_of_error(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    info = {}
    with pytest.raises(llm.SearchUnavailable):
        llm.chat_with_search(
            {"api_key": "k", "base_url": "http://127.0.0.1:9/compatible-mode/v1", "model": "m"},
            "x", api="dashscope", attempts=llm.search_forms("dashscope")[:1], info=info, timeout=3,
        )
    assert info["refused"][0].startswith("agent search: URLError")


def test_the_forms_for_each_route():
    assert [label for label, _ in llm.search_forms("dashscope")] == ["agent search", "enable_search + options", "enable_search only"]
    assert [label for label, _ in llm.search_forms("compat")] == ["enable_search + options", "enable_search only"]


# --- the explanation of an empty result mentions them -----------------------------------------------------------------


def test_an_empty_result_lists_the_forms_that_were_refused_first_native(fake_llm, tmp_path):
    _research_server(fake_llm, payload=EMPTY, sources=SEARCH_INFO)
    fake_llm.reject_strategy = True
    logs = []
    tr.auto_trends(_ai(fake_llm), niche="x", now=1e9, cache_dir=tmp_path, log=logs.append, api="dashscope")
    text = "\n".join(logs)
    assert "These request forms were refused first, so a simpler one was used:" in text
    assert "agent search: HTTP 400" in text and "search_strategy" in text


def test_an_empty_result_lists_the_forms_that_were_refused_first_compat(fake_llm, tmp_path):
    fake_llm.reject_keys = {"search_options"}
    text = _empty_run(fake_llm, tmp_path, EMPTY, sources=None)
    assert "These request forms were refused first" in text and "enable_search + options:" in text


def test_nothing_is_said_about_refusals_when_there_were_none(fake_llm, tmp_path):
    assert "refused first" not in _empty_run(fake_llm, tmp_path, EMPTY, sources=SEARCH_INFO)


def test_a_successful_refresh_also_shows_what_was_refused(monkeypatch, fake_llm, tmp_path, capsys):
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-test")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-flash")
    monkeypatch.setenv("CLIPPER_SEARCH_API", "dashscope")
    monkeypatch.chdir(tmp_path)
    _research_server(fake_llm)
    fake_llm.reject_strategy = True
    assert cli.main(["trends", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "request forms refused first (a simpler one was used):" in out and "agent search: HTTP 400" in out


# --- the command ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch, fake_llm, tmp_path):
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-test")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-flash")
    monkeypatch.chdir(tmp_path)
    return fake_llm


def test_the_command_prints_the_diagnosis(configured, monkeypatch, capsys):
    monkeypatch.setenv("CLIPPER_SEARCH_API", "dashscope")
    _research_server(configured)
    assert cli.main(["trends", "--diagnose", "--niche", "reality TV drama", "--region", "United States"]) == 0
    out = capsys.readouterr().out
    assert "Model: qwen-flash    route: dashscope" in out
    assert "[agent search] accepted" in out and "How to read this:" in out
    assert "reality TV drama" in out and "United States" in out


def test_the_command_needs_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["trends", "--diagnose"]) == 1
    assert "No API key configured" in capsys.readouterr().err


def test_the_command_works_even_when_you_have_your_own_trends_file(configured, tmp_path, capsys):
    (tmp_path / "trends.txt").write_text("#mine", encoding="utf-8")
    _research_server(configured)
    assert cli.main(["trends", "--diagnose"]) == 0
    assert "[enable_search + options] accepted" in capsys.readouterr().out  # compat route against the fake server


def test_the_command_leaves_no_cache_or_failure_marker(configured):
    _research_server(configured)
    cli.main(["trends", "--diagnose"])
    cache_dir = tr.cache_path(None, None).parent
    assert not cache_dir.exists() or not any(cache_dir.iterdir())


def test_the_flag_is_on_the_trends_command():
    assert cli.build_parser().parse_args(["trends", "--diagnose"]).diagnose is True
