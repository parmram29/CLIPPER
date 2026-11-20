"""The pipeline with Claude as the model: the real SDK, a local server that answers like Anthropic's API, and
the same scripted "model" the drama tests use (so what is checked is CLIPPER's behaviour, not a model's taste)."""

import json
import shutil

import pytest

from clipper import cli, config, drama, highlight_finder, llm, social, windowing
from clipper.gui import progress

from conftest import highlight, make_transcript
from test_drama_pipeline import EPISODE, Script, is_fight

KEY = "sk-ant-test-1234"  # pragma: allowlist secret (a made-up value)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

TRANSCRIPT = "\n".join(
    f"[00:{m:02d}:{s:02d},000 - 00:{m:02d}:{s + 4:02d},000] line {m}-{s}" for m in range(0, 6) for s in range(0, 60, 5)
)


def claude(server, **extra):
    return {"api_key": KEY, "base_url": server.base_url, "model": "claude-opus-5-5", "provider": "anthropic", **extra}


def find(server, **kwargs):
    return highlight_finder.find_highlights(
        transcript=TRANSCRIPT,
        video_info={"title": "Test Show", "channel": "Chan", "description": "desc"},
        num_clips=kwargs.pop("num_clips", 3),
        api_key=KEY,
        base_url=server.base_url,
        model="claude-opus-5-5",
        log=kwargs.pop("log", lambda m: None),
        **kwargs,
    )


# --- the highlight finder -------------------------------------------------------------------------


def test_the_finder_asks_claude_with_the_prompt_and_reads_a_fenced_answer(fake_claude):
    fake_claude.thinking = True  # the newest models think first; only the text block is the answer
    fake_claude.reply = "```json\n" + json.dumps([highlight("00:00:10,000", "00:01:15,000", title="the pick")]) + "\n```"
    logs = []
    valid, usage = find(fake_claude, log=logs.append)

    assert [h["title"] for h in valid] == ["the pick"]
    assert usage == {"prompt_tokens": 11, "completion_tokens": 22}
    (req,) = fake_claude.requests
    assert req["path"] == "/v1/messages"
    prompt = req["body"]["messages"][0]["content"]
    assert "line 3-25" in prompt and "Test Show" in prompt  # the transcript and the video context reached Claude
    assert "{transcript}" not in prompt and "{num_clips}" not in prompt
    assert any("Claude" in line for line in logs)


def test_a_sentence_before_the_json_is_not_reported_as_a_parse_error(fake_claude):
    fake_claude.reply = (
        "Here are the moments I picked:\n```json\n"
        + json.dumps([highlight("00:00:10,000", "00:01:15,000", title="one"), highlight("00:02:00,000", "00:03:10,000", title="two")])
        + "\n```\nI weighted the arguments highest."
    )
    logs = []
    valid, _ = find(fake_claude, log=logs.append)
    assert [h["title"] for h in valid] == ["one", "two"]
    assert not any("JSON parse error" in line or "malformed" in line for line in logs)


def test_a_reply_cut_off_in_the_middle_still_gives_the_complete_picks(fake_claude):
    complete = json.dumps(highlight("00:00:10,000", "00:01:15,000", title="whole"))
    fake_claude.reply = "[" + complete + ', {"start_time": "00:02:00,000", "end_ti'
    logs = []
    valid, _ = find(fake_claude, log=logs.append)
    assert [h["title"] for h in valid] == ["whole"]  # upstream's repair of broken JSON still works
    assert any("Recovered 1 clip object" in line for line in logs)


def test_the_finder_never_sends_a_temperature_to_claude_even_when_it_is_given_one(fake_claude):
    fake_claude.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    find(fake_claude, temperature=0.7)
    find(fake_claude, user_direction="only the fights")  # a direction used to make it sample colder
    for req in fake_claude.requests:
        assert not {"temperature", "top_p", "top_k"} & set(req["body"])


def test_a_claude_key_alone_sends_the_request_to_claude_for_callers_that_do_not_name_the_provider(fake_claude):
    fake_claude.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    find(fake_claude)  # no provider= argument
    assert len(fake_claude.requests) == 1 and fake_claude.requests[0]["path"] == "/v1/messages"


def test_effort_reaches_the_finders_request(fake_claude):
    fake_claude.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    find(fake_claude, provider="anthropic", effort="low")
    assert fake_claude.requests[0]["body"]["output_config"] == {"effort": "low"}


def test_a_claude_error_is_logged_and_raised_in_plain_words(fake_claude):
    fake_claude.fail(401, "authentication_error", "invalid x-api-key")
    logs = []
    with pytest.raises(RuntimeError, match="starts with sk-ant-"):
        find(fake_claude, log=logs.append)
    assert any(line.startswith("AI request failed: Anthropic rejected the API key") for line in logs)
    assert not any(KEY in line for line in logs)


def test_other_keys_still_use_the_openai_compatible_route(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    valid, _ = highlight_finder.find_highlights(
        transcript=TRANSCRIPT, video_info={}, num_clips=3, api_key="some-other-key", base_url=fake_llm.base_url, model="m", log=lambda m: None  # pragma: allowlist secret
    )
    assert len(valid) == 1
    assert fake_llm.requests[0]["path"].endswith("/chat/completions") and "temperature" in fake_llm.requests[0]["body"]


# --- long videos: windows, judging, the second look ----------------------------------------------------


def run_windowed(server, script, *, num_clips=8, minutes=EPISODE, **kwargs):
    server.reply_fn = script
    logs = []
    kwargs.setdefault("drama_share", drama.DEFAULT_SHARE)
    result = windowing.find_highlights_windowed(
        transcript=make_transcript(minutes),
        video_info={"title": "Episode", "channel": "C", "description": "d"},
        num_clips=num_clips,
        api_key=KEY,
        base_url=server.base_url,
        model="claude-opus-5-5",
        log=logs.append,
        provider="anthropic",
        **kwargs,
    )
    return result, logs


def test_the_whole_drama_check_runs_through_claude(fake_claude):
    # five calm picks first and then six fights, in each of two windows: the failure the drama check was built for
    (picks, usage), logs = run_windowed(fake_claude, Script(first=["calm"] * 5 + ["fight"] * 6))

    assert len(picks) == 8 and sum(is_fight(h) for h in picks) >= 6  # 6 of 8
    assert drama.check(picks).passed and all(drama.is_judged(h) for h in picks)
    assert any(line.startswith("Drama check: ") and line.endswith("PASS") for line in logs)
    assert len(fake_claude.requests) == 3  # two windows and one judging call
    assert not any({"temperature", "top_p", "top_k"} & set(r["body"]) for r in fake_claude.requests)
    assert usage == {"prompt_tokens": 3 * 11, "completion_tokens": 3 * 22}  # every call's tokens are counted


def test_the_second_look_for_confrontations_also_goes_to_claude(fake_claude):
    script = Script(first=["calm"] * 4 + ["fight"] * 3, again=["fight"] * 5)  # three fights per window: six in all
    (picks, _), _ = run_windowed(fake_claude, script, num_clips=10)  # ten clips need eight fights; only six were found
    assert script.steered_prompts, "the second look should have been asked for"
    assert all(r["path"] == "/v1/messages" for r in fake_claude.requests)
    assert len(picks) > 0


def test_a_window_that_fails_does_not_stop_the_others(fake_claude):
    fake_claude.fail(529, "overloaded_error", "Overloaded")  # the first window's request
    (picks, _), logs = run_windowed(fake_claude, Script(first=["fight"] * 4), drama_share=None, rerank=False)
    assert picks, "the second window still produced picks"
    assert any(line.startswith("Window 1/2 failed") and "busy or down" in line for line in logs)


def test_every_window_failing_ends_with_claudes_reason(fake_claude):
    fake_claude.always_fail = (401, "authentication_error", "invalid x-api-key", {})
    with pytest.raises(RuntimeError, match="No valid highlights found in any window.*Anthropic rejected the API key"):
        run_windowed(fake_claude, Script(first=["fight"]), drama_share=None)


def test_a_judge_that_fails_keeps_the_per_window_scores(fake_claude):
    script = Script(first=["fight"] * 4, judge="garbage")  # Claude answers the judging call with prose, not JSON
    (picks, _), logs = run_windowed(fake_claude, script)
    assert picks
    assert any("Cross-window re-scoring failed" in line or "keeping per-window scores" in line for line in logs)


# --- post copy ------------------------------------------------------------------------------------------


def test_social_copy_is_written_by_claude_as_json_only(fake_claude):
    fake_claude.thinking = True
    fake_claude.reply = json.dumps(
        {
            "title": "Ring size?!",
            "captions": [{"style": "tease", "text": "She asked one question and the room went quiet"}],
            "hashtags": ["#Dating", "WTF"],
            "keywords": ["awkward first date"],
            "pinned_comment": "Be honest: would you answer?",
        }
    )
    copy = social.generate_social_copy({"title": "t", "hook_text": "h", "description": "d", "transcript_text": "words " * 20}, claude(fake_claude), language="English")

    assert copy["title"] == "Ring size?!" and copy["hashtags"] == ["dating", "wtf"]
    (req,) = fake_claude.requests
    assert "JSON only" in req["body"]["system"] and "temperature" not in req["body"]
    assert req["body"]["max_tokens"] == 700 + 12_000  # the length of the answer plus room to think


# --- what only OpenAI-compatible services can do ----------------------------------------------------------


def test_chat_goes_by_the_settings_dict(fake_claude, fake_llm):
    fake_claude.reply = "claude says hi"
    fake_llm.reply = "the other one says hi"
    assert llm.chat(claude(fake_claude), "hello")[0] == "claude says hi"
    other = {"api_key": "k", "base_url": fake_llm.base_url, "model": "m"}
    assert llm.chat(other, "hello")[0] == "the other one says hi"
    assert len(fake_claude.requests) == 1 and len(fake_llm.requests) == 1


def test_a_claude_key_is_never_sent_down_a_web_search_path_built_for_another_provider(fake_claude):
    for api in ("compat", "dashscope"):
        with pytest.raises(llm.SearchUnavailable, match="not used with Claude"):
            llm.chat_with_search(claude(fake_claude), "what is trending?", api=api)
    assert fake_claude.requests == []


def test_ai_web_research_is_off_for_claude_and_says_so():
    env = {"CLIPPER_API_KEY": KEY}
    assert cli.search_ai_config(env) is None
    assert "not used with Claude" in cli.search_problem(env)
    assert "No API key configured" in cli.search_problem({})  # a different reason, still stated


# --- the setup check ----------------------------------------------------------------------------------------


@needs_ffmpeg
def test_doctor_check_ai_asks_claude_one_tiny_question(fake_claude, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # no cookies.txt or .env here
    monkeypatch.setenv("CLIPPER_API_KEY", KEY)
    monkeypatch.setenv("CLIPPER_PROVIDER", "anthropic")  # an address of your own is only followed when you say so
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_claude.base_url)
    fake_claude.reply = "ok"

    code = cli.main(["doctor", "--check-ai"])
    out = capsys.readouterr().out

    assert "[FAIL]" not in out, out
    assert code == 0
    assert "Claude (Anthropic): model claude-opus-5-5" in out and "key ...1234" in out
    assert "Claude answered: 'ok'" in out
    assert KEY not in out  # the key is never printed
    (req,) = fake_claude.requests
    assert req["body"]["max_tokens"] == 5 + 12_000  # a tiny answer, with room to think first


@needs_ffmpeg
def test_doctor_reports_claudes_reason_when_the_key_is_refused(fake_claude, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLIPPER_API_KEY", KEY)
    monkeypatch.setenv("CLIPPER_PROVIDER", "anthropic")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_claude.base_url)
    fake_claude.fail(401, "authentication_error", "invalid x-api-key")
    code = cli.main(["doctor", "--check-ai"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[FAIL] AI endpoint call failed: Anthropic rejected the API key" in out and "sk-ant-" in out


@needs_ffmpeg
def test_doctor_says_which_leftover_settings_it_ignored(monkeypatch, tmp_path, capsys):
    # the situation this was written for: the Alibaba lines are still in .env, but the key is now a Claude key
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLIPPER_API_KEY", KEY)
    monkeypatch.setenv("CLIPPER_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-flash")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "AI: Claude (Anthropic): model claude-opus-5-5" in out
    assert "Ignoring CLIPPER_BASE_URL (dashscope-intl.aliyuncs.com)" in out
    assert "Ignoring CLIPPER_MODEL=qwen-flash" in out
    assert "qwen" not in out.split("AI:")[1].split("\n")[0]  # the AI line itself names Claude, not the leftover model


def test_the_configured_effort_reaches_the_request(fake_claude, monkeypatch):
    monkeypatch.setenv("CLIPPER_API_KEY", KEY)
    monkeypatch.setenv("CLIPPER_EFFORT", "low")
    monkeypatch.setenv("CLIPPER_PROVIDER", "anthropic")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_claude.base_url)
    ai = config.load_ai_config().as_dict()
    fake_claude.reply = "hello"
    llm.chat(ai, "hi")
    assert fake_claude.requests[0]["body"]["output_config"] == {"effort": "low"}


# --- the app's plain-English hints must match what Claude really says -----------------------------------------


def _failing_run(server):
    logs = []
    with pytest.raises(RuntimeError) as caught:
        windowing.find_highlights_windowed(
            transcript=make_transcript(5), video_info={}, num_clips=2, api_key=KEY, base_url=server.base_url,
            model="claude-opus-5-5", log=logs.append, provider="anthropic",
        )
    return logs + [f"error: {caught.value}"]  # what the app's log holds when a run fails


@pytest.mark.parametrize(
    "status, kind, message, extra, expected",
    [
        (401, "authentication_error", "invalid x-api-key", {}, "Claude refused your key"),  # not "No AI key is set yet"
        (402, "billing_error", "billing problem", {}, "out of credit"),
        (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.", {}, "out of credit"),
        (404, "not_found_error", "model: claude-nope", {}, "doesn't know the model"),
        (429, "rate_limit_error", "slow down", {}, "busy or slow"),
        (429, "rate_limit_error", "cap", {"details": {"error_code": "enforced_spend_limit_reached"}}, "spend limit"),
        (529, "overloaded_error", "Overloaded", {}, "busy or slow"),
        (500, "api_error", "internal error", {}, "busy or slow"),
    ],
)
def test_the_apps_hint_for_each_claude_problem(fake_claude, status, kind, message, extra, expected):
    fake_claude.always_fail = (status, kind, message, extra)
    assert expected in progress.friendly_error(_failing_run(fake_claude))


def test_the_apps_hint_when_anthropic_cannot_be_reached(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr("clipper.claude_client.RETRIES", 0)
    lines = []
    with pytest.raises(RuntimeError) as caught:
        windowing.find_highlights_windowed(
            transcript=make_transcript(5), video_info={}, num_clips=2, api_key=KEY, base_url="http://127.0.0.1:1",
            model="claude-opus-5-5", log=lines.append, provider="anthropic",
        )
    assert "couldn't reach Anthropic" in progress.friendly_error(lines + [f"error: {caught.value}"])
