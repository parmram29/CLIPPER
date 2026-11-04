import json

import pytest

from clipper import social


def _ai(server):
    return {"api_key": "k", "base_url": server.base_url, "model": "qwen-flash"}


HIGHLIGHT = {
    "title": "She said 'what's your ring size?'",
    "hook_text": "She asked 'what's your ring size?' and laughed",
    "description": "an awkward proposal moment",
    "category": "funny",
    "transcript_text": "so what is your ring size " * 5,
}


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["Drama", "#Reality TV", "drama", "  ", "x y"], ["drama", "realitytv", "xy"]),
        ("funny, #wtf  datingshow", ["funny", "wtf", "datingshow"]),
        (["a", "b", "c", "d", "e", "f", "g", "h"], ["a", "b", "c", "d", "e", "f"]),  # at most six
        (["café", "日本語"], ["café", "日本語"]),  # non-English words survive
        (None, []),
    ],
)
def test_normalize_hashtags(raw, expected):
    assert social.normalize_hashtags(raw) == expected


def test_truncate_cuts_on_a_word_and_stays_within_the_limit():
    out = social._truncate("one two three four five six seven", 15)
    assert len(out) <= 15 and out.endswith("…") and " " not in out[-2:]
    assert social._truncate("short", 15) == "short"
    assert social._truncate("  spaced   out  ", 50) == "spaced out"


def test_generation_sends_the_clip_facts_in_json_mode_and_cleans_the_reply(fake_llm):
    fake_llm.reply = json.dumps(
        {"title": "Ring size?!", "caption": "She asked and the room froze", "hashtags": ["#Dating", "WTF", "dating"]}
    )
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm), language="English")

    assert copy == {
        "title": "Ring size?!",
        "caption": "She asked and the room froze",
        "hashtags": ["dating", "wtf"],
        "platform": "tiktok",
    }
    (request,) = fake_llm.requests
    assert request["body"]["response_format"] == {"type": "json_object"}
    prompt = request["body"]["messages"][-1]["content"]
    assert "ring size" in prompt and "an awkward proposal moment" in prompt and "funny" in prompt
    assert "casual English" in prompt and "for tiktok" in prompt


def test_limits_are_enforced_in_code_even_if_the_model_ignores_them(fake_llm):
    fake_llm.reply = json.dumps(
        {"title": "word " * 40, "caption": "sentence " * 60, "hashtags": [f"tag{i}" for i in range(20)]}
    )
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert len(copy["title"]) <= social.TITLE_MAX
    assert len(copy["caption"]) <= social.CAPTION_MAX
    assert len(copy["hashtags"]) == social.MAX_HASHTAGS


def test_it_retries_without_json_mode_when_the_provider_rejects_it(fake_llm):
    fake_llm.reject_response_format = True
    fake_llm.reply = json.dumps({"title": "T", "caption": "C", "hashtags": ["x"]})
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert copy["caption"] == "C"
    assert len(fake_llm.requests) == 2
    assert "response_format" in fake_llm.requests[0]["body"] and "response_format" not in fake_llm.requests[1]["body"]


def test_fenced_json_replies_are_accepted(fake_llm):
    fake_llm.reply = '```json\n{"title": "T", "caption": "C", "hashtags": ["x"]}\n```'
    assert social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))["title"] == "T"


def test_missing_fields_fall_back_to_the_highlight(fake_llm):
    fake_llm.reply = json.dumps({"hashtags": ["x"]})
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert copy["title"] == HIGHLIGHT["title"]
    assert copy["caption"] == HIGHLIGHT["hook_text"]


def test_a_reply_that_is_not_json_raises(fake_llm):
    fake_llm.reply = "I can't help with that."
    with pytest.raises(ValueError):
        social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))


def test_caption_line_puts_hashtags_after_the_caption():
    assert social.caption_line({"caption": "Wild night", "hashtags": ["drama", "wtf"]}) == "Wild night #drama #wtf"
    assert social.caption_line({"caption": "Just text"}) == "Just text"


def test_save_and_load_round_trip(tmp_path):
    copy = {"title": "Café 😮", "caption": "c", "hashtags": ["x"]}
    path = social.save_social(tmp_path, 4, copy)
    assert path == tmp_path / "social" / "clip_004.json"
    assert social.load_social(tmp_path, 4) == copy


def test_loading_missing_or_corrupt_copy_gives_none(tmp_path):
    assert social.load_social(tmp_path, 0) is None
    social.save_social(tmp_path, 1, {"title": "x"})
    social.social_path(tmp_path, 1).write_text("{ broken", encoding="utf-8")
    assert social.load_social(tmp_path, 1) is None
