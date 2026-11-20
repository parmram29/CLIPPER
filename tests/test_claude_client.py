"""CLIPPER's Claude requests, made by the real ``anthropic`` SDK against a local server that answers the
way Anthropic's API does (server-sent events, Anthropic's error format). Nothing here costs money."""

import sys
from types import SimpleNamespace

import pytest

from clipper import claude_client

KEY = "sk-ant-test-1234"  # pragma: allowlist secret (a made-up value)


def credentials(headers):
    """Every credential a request carried, whichever header style the installed SDK uses
    (older releases send ``x-api-key``; 2.x sends ``Authorization: Bearer``)."""
    found = []
    if "x-api-key" in headers:
        found.append(headers["x-api-key"])
    if "authorization" in headers:
        found.append(headers["authorization"].removeprefix("Bearer ").strip())
    return found


def ask(server, text="hello", **kwargs):
    kwargs.setdefault("model", "claude-opus-5-5")
    return claude_client.message(api_key=KEY, base_url=server.base_url, user=text, **kwargs)


def test_request_matches_anthropics_messages_api(fake_claude):
    fake_claude.reply = '{"ok": true}'
    text, usage = ask(fake_claude, "hello", system="be brief", max_tokens=700)

    (req,) = fake_claude.requests
    assert req["path"] == "/v1/messages"
    assert credentials(req["headers"]) == [KEY]  # the key, once, and no second credential beside it
    assert "anthropic-version" in req["headers"]
    body = req["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["system"] == "be brief"
    assert body["stream"] is True
    assert (text, usage) == ('{"ok": true}', {"prompt_tokens": 11, "completion_tokens": 22})


def test_never_sends_settings_that_current_claude_models_reject(fake_claude):
    # temperature, top_p and top_k each make the newest models answer HTTP 400; thinking cannot be switched off
    ask(fake_claude)
    body = fake_claude.requests[0]["body"]
    for banned in ("temperature", "top_p", "top_k", "thinking", "output_config"):
        assert banned not in body, f"{banned} must not be sent unless it was asked for"


def test_effort_is_sent_only_when_asked_for(fake_claude):
    ask(fake_claude, effort="low")
    assert fake_claude.requests[0]["body"]["output_config"] == {"effort": "low"}
    with pytest.raises(claude_client.ClaudeError, match="effort must be one of"):
        ask(fake_claude, effort="turbo")
    assert len(fake_claude.requests) == 1  # the bad value never reached the network


def test_the_answer_is_the_text_block_not_the_thinking(fake_claude):
    fake_claude.thinking = True
    fake_claude.reply = '[{"title": "the answer"}]'
    text, _ = ask(fake_claude)
    assert text == '[{"title": "the answer"}]'
    assert "weigh" not in text and "not the answer" not in text


def test_room_to_think_is_added_to_the_answer_length(fake_claude):
    ask(fake_claude, max_tokens=700)
    ask(fake_claude)
    sent = [r["body"]["max_tokens"] for r in fake_claude.requests]
    assert sent == [700 + claude_client.THINKING_ROOM, claude_client.DEFAULT_ANSWER_TOKENS + claude_client.THINKING_ROOM]
    assert claude_client.max_tokens_for(10**9) == claude_client.MAX_OUTPUT_TOKENS  # never above the ceiling
    assert claude_client.max_tokens_for(0) == claude_client.max_tokens_for(None)


def test_json_only_tells_claude_to_reply_with_nothing_else(fake_claude):
    ask(fake_claude, system="You write captions.", json_only=True)
    ask(fake_claude, json_only=True)
    first, second = (r["body"]["system"] for r in fake_claude.requests)
    assert first.startswith("You write captions.") and "JSON only" in first
    assert "JSON only" in second  # also when there was no system prompt of its own


def test_usage_counts_cached_input_too(fake_claude):
    fake_claude.usage = (100, 50)
    assert ask(fake_claude)[1] == {"prompt_tokens": 100, "completion_tokens": 50}


def test_a_refusal_is_an_error_that_says_why(fake_claude):
    fake_claude.reply = ""
    fake_claude.stop_reason = "refusal"
    fake_claude.stop_details = {"type": "refusal", "category": "general_harms", "explanation": "it looked like a harmful request"}
    with pytest.raises(claude_client.ClaudeError, match="declined.*it looked like a harmful request"):
        ask(fake_claude)


def test_a_refusal_without_details_is_still_an_error(fake_claude):
    fake_claude.reply = ""
    fake_claude.stop_reason = "refusal"
    with pytest.raises(claude_client.ClaudeError, match="declined"):
        ask(fake_claude)


def test_running_out_of_room_is_an_error_even_if_some_text_came_back(fake_claude):
    fake_claude.reply = '[{"title": "cut off mid'
    fake_claude.stop_reason = "max_tokens"
    with pytest.raises(claude_client.ClaudeError, match="ran out of room.*CLIPPER_EFFORT=low"):
        ask(fake_claude)


def test_an_empty_reply_is_an_error(fake_claude):
    fake_claude.reply = ""
    with pytest.raises(claude_client.ClaudeError, match="no text"):
        ask(fake_claude)


@pytest.mark.parametrize(
    "status, kind, message, extra, expected",
    [
        (401, "authentication_error", "invalid x-api-key", {}, "starts with sk-ant-"),
        (402, "billing_error", "billing problem", {}, "out of credit"),
        # Anthropic reports a low balance as a 400, with this wording
        (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.", {}, "out of credit"),
        (403, "permission_error", "your workspace may not use this", {}, "not allowed to do that.*your workspace may not use this"),
        (404, "not_found_error", "model: claude-nope", {}, "does not know the model 'claude-opus-5-5'.*CLIPPER_MODEL=claude-opus-5-5"),
        (429, "rate_limit_error", "slow down", {}, "too fast"),
        (429, "rate_limit_error", "monthly spend cap reached", {"details": {"error_code": "enforced_spend_limit_reached"}}, "spend limit"),
        (400, "invalid_request_error", "messages: roles must alternate", {}, r"refused the request \(400\): messages: roles must alternate"),
        (500, "api_error", "internal error", {}, r"busy or down.*HTTP 500"),
        (529, "overloaded_error", "Overloaded", {}, r"busy or down.*HTTP 529"),
    ],
)
def test_errors_are_explained_in_plain_words(fake_claude, status, kind, message, extra, expected):
    fake_claude.fail(status, kind, message, **extra)
    with pytest.raises(claude_client.ClaudeError, match=expected) as caught:
        ask(fake_claude)
    assert KEY not in str(caught.value)  # the key never appears in an error


def test_an_unreachable_server_is_explained(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(claude_client, "RETRIES", 0)
    with pytest.raises(claude_client.ClaudeError, match="Could not reach Anthropic"):
        claude_client.message(api_key=KEY, base_url="http://127.0.0.1:1", model="claude-opus-5-5", user="hi")


def test_the_environment_cannot_change_the_credential_or_the_address(fake_claude, monkeypatch):
    # Someone who also uses Claude Code or another Anthropic tool may have these set. CLIPPER's own key and
    # address win, so the key is never sent somewhere else and no second credential rides along.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key-9999")  # pragma: allowlist secret
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-from-the-environment")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")
    ask(fake_claude)
    assert credentials(fake_claude.requests[0]["headers"]) == [KEY]


def test_the_address_may_end_with_a_slash(fake_claude):
    claude_client.message(api_key=KEY, base_url=fake_claude.base_url + "/", model="claude-opus-5-5", user="hi")
    assert fake_claude.requests[0]["path"] == "/v1/messages"


def test_retries_are_left_to_the_sdk_and_bounded(monkeypatch):
    monkeypatch.setattr(claude_client, "RETRIES", 3)
    sdk_client = claude_client.client(KEY)
    assert sdk_client.max_retries == 3
    assert str(sdk_client.base_url).rstrip("/") == claude_client.DEFAULT_BASE_URL


def test_a_missing_package_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail
    with pytest.raises(claude_client.ClaudeError, match="CLIPPER.command"):
        claude_client.message(api_key=KEY, model="claude-opus-5-5", user="hi")


def test_from_ai_uses_the_settings_dict(fake_claude):
    text, _ = claude_client.from_ai(
        {"api_key": KEY, "base_url": fake_claude.base_url, "model": "claude-haiku-5-5", "effort": "high"}, "hi", max_tokens=50
    )
    body = fake_claude.requests[0]["body"]
    assert body["model"] == "claude-haiku-5-5" and body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 50 + claude_client.THINKING_ROOM


# --- a reply that is not bare JSON ------------------------------------------------------------------------


ARRAY = '[{"title": "a", "start_time": "00:00:10,000"}, {"title": "b", "start_time": "00:02:00,000"}]'


@pytest.mark.parametrize(
    "reply",
    [
        ARRAY,
        "```json\n" + ARRAY + "\n```",
        "Here are the moments:\n\n" + ARRAY,
        "Here are the top [2] moments I found:\n```json\n" + ARRAY + "\n```\nLet me know if you want more.",
        "  \n" + ARRAY + "\n\nThose are all in order.",
    ],
)
def test_the_json_array_is_found_inside_a_chatty_reply(reply):
    assert claude_client.json_array_part(reply) == ARRAY


@pytest.mark.parametrize(
    "reply",
    [
        '[{"title": "a"}, {"title": "b", "start_ti',  # cut off: the caller's own repair needs all of it
        "I could not find any moments.",
        "[1, 2, 3]",  # an array, but not of objects
        "[]",
        '{"title": "a", "start_time": "00:00:10,000"}',  # a single object, not an array
        "",
    ],
)
def test_text_without_a_complete_array_of_objects_is_left_alone(reply):
    assert claude_client.json_array_part(reply) == reply


@pytest.mark.parametrize(
    "status, kind, message, extra, must_survive",
    [
        (401, "authentication_error", "invalid x-api-key", {}, "starts with sk-ant-"),
        (402, "billing_error", "billing", {}, "platform.claude.com/settings/billing"),
        (404, "not_found_error", "model: x", {}, "CLIPPER_MODEL=claude-opus-5-5"),
        (429, "rate_limit_error", "cap", {"details": {"error_code": "enforced_spend_limit_reached"}}, "platform.claude.com"),
        (529, "overloaded_error", "Overloaded", {}, "Run it again in a minute"),
    ],
)
def test_the_point_of_each_message_fits_in_the_window_logs_160_characters(fake_claude, status, kind, message, extra, must_survive):
    fake_claude.fail(status, kind, message, **extra)
    with pytest.raises(claude_client.ClaudeError) as caught:
        ask(fake_claude)
    assert must_survive in str(caught.value)[:160], str(caught.value)


def test_only_blocks_of_type_text_are_the_answer_whatever_else_has_a_text_field():
    # a thinking block has no .text, so a real reply cannot show this; a block of some other kind that does must still be left out
    reply = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking="hmm", text="NOT THE ANSWER"),
            SimpleNamespace(type="text", text="the answer"),
            SimpleNamespace(type="something_else", text="ALSO NOT THE ANSWER"),
        ],
    )
    assert claude_client.reply_text(reply) == "the answer"
