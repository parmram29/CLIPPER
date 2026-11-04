"""The highlight finder talks to any OpenAI-compatible endpoint. These tests use a
real local HTTP server, so they exercise the actual request the Qwen/DashScope
compatible mode would receive: path, bearer token, model, and prompt contents."""

import json

from clipper import highlight_finder
from clipper.prompts import DEFAULT_HIGHLIGHT_PROMPT

from conftest import highlight

TRANSCRIPT = "\n".join(
    f"[00:{m:02d}:{s:02d},000 - 00:{m:02d}:{s + 4:02d},000] line {m}-{s}"
    for m in range(0, 6)
    for s in range(0, 60, 5)
)


def _call(server, **kwargs):
    return highlight_finder.find_highlights(
        transcript=TRANSCRIPT,
        video_info={"title": "Test Show", "channel": "Chan", "description": "desc"},
        num_clips=kwargs.pop("num_clips", 3),
        api_key="sk-test-key",
        base_url=server.base_url,
        model="qwen-plus",
        log=lambda m: None,
        **kwargs,
    )


def test_request_shape_matches_openai_compatible_contract(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    _call(fake_llm)

    (req,) = fake_llm.requests
    assert req["path"] == "/compatible-mode/v1/chat/completions"
    assert req["auth"] == "Bearer sk-test-key"
    assert req["body"]["model"] == "qwen-plus"
    prompt = req["body"]["messages"][0]["content"]
    assert "line 3-25" in prompt  # the transcript reached the model
    assert "Test Show" in prompt  # so did the video context
    assert "{transcript}" not in prompt and "{num_clips}" not in prompt  # all placeholders filled


def test_asks_for_extra_clips_then_filters_by_duration(fake_llm):
    fake_llm.reply = json.dumps(
        [
            highlight("00:00:10,000", "00:01:15,000", title="65s ok"),
            highlight("00:02:00,000", "00:02:30,000", title="30s too short"),
            highlight("00:03:00,000", "00:04:40,000", title="100s ok"),
            highlight("00:05:00,000", "00:07:10,000", title="130s too long"),
        ]
    )
    valid, usage = _call(fake_llm, num_clips=3)

    assert [h["title"] for h in valid] == ["65s ok", "100s ok"]
    assert [h["duration_seconds"] for h in valid] == [65.0, 100.0]
    # it over-requests (num_clips + 3) so invalid-length clips can be dropped
    assert "EXACTLY 6 segments" in fake_llm.requests[0]["body"]["messages"][0]["content"]
    assert usage == {"prompt_tokens": 11, "completion_tokens": 22}


def test_salvages_valid_clips_from_a_malformed_response(fake_llm):
    # An unescaped double quote inside a field breaks json.loads for the whole array.
    good = json.dumps(highlight("00:00:10,000", "00:01:15,000", title="good one"))
    broken = (
        '{"start_time":"00:03:00,000","end_time":"00:04:10,000",'
        '"title":"He said "oops" loudly","description":"d","virality_score":6,"hook_text":"h"}'
    )
    fake_llm.reply = f"[{good},{broken}]"
    valid, _ = _call(fake_llm)
    assert [h["title"] for h in valid] == ["good one"]


def test_strips_markdown_fences(fake_llm):
    fake_llm.reply = "```json\n" + json.dumps([highlight("00:00:10,000", "00:01:15,000")]) + "\n```"
    valid, _ = _call(fake_llm)
    assert len(valid) == 1


def test_user_direction_reaches_the_model_and_lowers_temperature(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:02:00,000", "00:02:50,000")])
    valid, _ = _call(fake_llm, user_direction="use 2:00 - 2:50 and skip the sponsor read")

    body = fake_llm.requests[0]["body"]
    prompt = body["messages"][0]["content"]
    assert "USER DIRECTION" in prompt and "skip the sponsor read" in prompt
    assert body["temperature"] == highlight_finder.DIRECTED_TEMPERATURE
    # an explicitly requested range is exempt from the 58-120s rule (50s here)
    assert [h["duration_seconds"] for h in valid] == [50.0]


def test_custom_system_prompt_replaces_the_default(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000")])
    custom = "NICHE PROMPT: find {num_clips} clips about cooking.\n{transcript}"
    _call(fake_llm, system_prompt=custom)
    prompt = fake_llm.requests[0]["body"]["messages"][0]["content"]
    assert prompt.startswith("NICHE PROMPT")
    assert DEFAULT_HIGHLIGHT_PROMPT[:40] not in prompt
