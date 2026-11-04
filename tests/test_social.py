import json
import os
import time

import pytest

from clipper import social, trends as trends_mod
from clipper.viral_prompt import CATEGORIES


def _ai(server):
    return {"api_key": "k", "base_url": server.base_url, "model": "qwen-flash"}


HIGHLIGHT = {
    "title": "She said 'what's your ring size?'",
    "hook_text": "She asked 'what's your ring size?' and laughed",
    "description": "an awkward proposal moment",
    "category": "funny",
    "virality_score": 8,
    "transcript_text": "so what is your ring size " * 5,
}

FULL_REPLY = {
    "title": "Ring size?!",
    "captions": [
        {"style": "tease", "text": "She asked one question and the whole room went quiet"},
        {"style": "question", "text": "Would you answer this on a first date?"},
        {"style": "hot take", "text": "Asking this early is a red flag. Fight me."},
    ],
    "hashtags": ["#Dating", "WTF", "dating", "realitytv"],
    "keywords": ["awkward first date", "ring size question"],
    "pinned_comment": "Be honest: would you answer?",
}


def _trends(tmp_path, text, age_days=1.0):
    path = tmp_path / "trends.txt"
    path.write_text(text, encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))
    return trends_mod.load_trends(path)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (["Drama", "#Reality TV", "drama", "  ", "x y"], ["drama", "realitytv", "xy"]),
        ("funny, #wtf  datingshow", ["funny", "wtf", "datingshow"]),
        (["a", "b", "c", "d", "e", "f", "g", "h"], ["a", "b", "c", "d", "e"]),  # at most five
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


# --- the prompt: what it is built from ---------------------------------------------


def _prompt(highlight=HIGHLIGHT, *, niche=None, trends=None, language="English"):
    return social.build_prompt(highlight, language=language, platform="tiktok", niche=niche, trends=trends)


def test_the_prompt_uses_the_clips_own_content_category_and_score():
    prompt = _prompt()
    assert "ring size" in prompt and "an awkward proposal moment" in prompt
    assert "Category: funny" in prompt and "Estimated virality score: 8/10" in prompt
    assert "casual English" in prompt and "for tiktok" in prompt


def test_the_prompt_asks_for_three_angles_that_do_not_spoil_the_payoff():
    prompt = _prompt()
    for angle in ('"tease"', '"question"', '"hot take"'):
        assert angle in prompt
    assert "Do NOT reveal the punchline" in prompt
    assert "The first 100 characters carry the hook" in prompt


def test_the_prompt_asks_for_search_words_and_a_comment_prompt():
    prompt = _prompt()
    assert "search keyword" in prompt and "keywords" in prompt and "pinned_comment" in prompt


def test_the_prompt_does_not_claim_exact_algorithm_weights():
    # the factors are secondary-source guidance, and no weights are public
    assert "exact weights are not public" in _prompt()
    assert "%" not in _prompt().split("Clip facts:")[0]


@pytest.mark.parametrize("category", [c for c in CATEGORIES if c != "other"])
def test_every_category_the_finder_can_assign_has_a_caption_tone(category):
    assert category in social.TONE_BY_CATEGORY
    assert social.TONE_BY_CATEGORY[category] in _prompt({**HIGHLIGHT, "category": category})


def test_an_unknown_category_gets_the_default_tone():
    assert social.DEFAULT_TONE in _prompt({**HIGHLIGHT, "category": "other"})
    assert social.DEFAULT_TONE in _prompt({k: v for k, v in HIGHLIGHT.items() if k != "category"})


def test_funny_clips_are_told_to_leave_the_punchline_out():
    assert "leave the punchline out" in _prompt({**HIGHLIGHT, "category": "funny"})


# --- trends and niche ------------------------------------------------------------------


def test_without_a_trends_file_the_model_is_told_not_to_claim_anything_is_trending():
    prompt = _prompt()
    assert "No current trend list was supplied" in prompt
    assert "Do not claim that anything is trending" in prompt


def test_the_models_own_knowledge_of_trends_is_always_ruled_out():
    for prompt in (_prompt(), _prompt(trends=trends_mod.Trends(None, "#x", 1, ("x",), False, False))):
        assert "Your own knowledge of what is trending is out of date" in prompt


def test_supplied_trends_reach_the_prompt_with_their_age(tmp_path):
    loaded = _trends(tmp_path, "Trending hashtags:\n#dramaalert #datingfails\n", age_days=3)
    prompt = _prompt(trends=loaded)
    assert "#dramaalert #datingfails" in prompt
    assert "last updated 3 day(s) ago" in prompt
    assert "genuinely fits THIS clip" in prompt
    assert "No current trend list" not in prompt


def test_stale_trends_are_flagged_to_the_model(tmp_path):
    assert "STALE" in _prompt(trends=_trends(tmp_path, "#old", age_days=30))
    assert "STALE" not in _prompt(trends=_trends(tmp_path, "#fresh", age_days=2))


def test_the_prompt_limits_trending_tags_to_one_and_only_if_listed_and_fitting():
    prompt = _prompt()
    assert "at most ONE trending tag" in prompt and "appears in the trend list" in prompt


def test_the_niche_is_passed_on_when_given():
    assert "Account niche: reality TV drama" in _prompt(niche="  reality TV drama ")
    assert "Account niche" not in _prompt(niche="   ")
    assert "Account niche" not in _prompt()


# --- generation --------------------------------------------------------------------------


def test_generation_returns_three_angles_hashtags_keywords_and_a_pinned_comment(fake_llm):
    fake_llm.reply = json.dumps(FULL_REPLY)
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm), language="English")

    assert copy["title"] == "Ring size?!"
    assert [v["style"] for v in copy["variants"]] == ["tease", "question", "hot take"]
    assert copy["caption"] == copy["variants"][0]["caption"] == "She asked one question and the whole room went quiet"
    assert copy["hashtags"] == ["dating", "wtf", "realitytv"]  # cleaned, deduped, lowercased
    assert copy["keywords"] == ["awkward first date", "ring size question"]
    assert copy["pinned_comment"] == "Be honest: would you answer?"
    assert copy["platform"] == "tiktok"
    assert copy["trend_tags_used"] == [] and copy["trends_age_days"] is None

    (request,) = fake_llm.requests
    assert request["body"]["response_format"] == {"type": "json_object"}
    assert "ring size" in request["body"]["messages"][-1]["content"]


def test_it_records_which_supplied_trend_tags_the_model_used(fake_llm, tmp_path):
    fake_llm.reply = json.dumps({**FULL_REPLY, "hashtags": ["dating", "DramaAlert", "madeuptrend"]})
    loaded = _trends(tmp_path, "#dramaalert #datingfails", age_days=2)
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm), trends=loaded)
    # "madeuptrend" was not in the user's list, so it is not counted as a trend tag
    assert copy["trend_tags_used"] == ["dramaalert"]
    assert copy["trends_age_days"] == 2.0


def test_limits_are_enforced_in_code_even_if_the_model_ignores_them(fake_llm):
    fake_llm.reply = json.dumps(
        {
            "title": "word " * 40,
            "captions": [{"style": "s" * 50, "text": "sentence " * 60}] * 6,
            "hashtags": [f"tag{i}" for i in range(20)],
            "keywords": ["k" * 100, "K" * 100, "other", "third", "fourth"],
            "pinned_comment": "comment " * 40,
        }
    )
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert len(copy["title"]) <= social.TITLE_MAX
    assert len(copy["variants"]) == social.MAX_VARIANTS
    assert all(len(v["caption"]) <= social.CAPTION_MAX and len(v["style"]) <= 20 for v in copy["variants"])
    assert len(copy["hashtags"]) == social.MAX_HASHTAGS
    assert len(copy["keywords"]) <= social.MAX_KEYWORDS and all(len(k) <= social.KEYWORD_MAX for k in copy["keywords"])
    assert len(copy["pinned_comment"]) <= social.PINNED_MAX


def test_a_model_that_sends_one_plain_caption_still_works(fake_llm):
    fake_llm.reply = json.dumps({"title": "T", "caption": "Just one caption", "hashtags": ["x"]})
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert copy["caption"] == "Just one caption"
    assert copy["variants"] == [{"style": "caption", "caption": "Just one caption"}]
    assert copy["keywords"] == [] and copy["pinned_comment"] == ""


def test_it_retries_without_json_mode_when_the_provider_rejects_it(fake_llm):
    fake_llm.reject_response_format = True
    fake_llm.reply = json.dumps(FULL_REPLY)
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert copy["title"] == "Ring size?!"
    assert len(fake_llm.requests) == 2
    assert "response_format" in fake_llm.requests[0]["body"] and "response_format" not in fake_llm.requests[1]["body"]


def test_fenced_json_replies_are_accepted(fake_llm):
    fake_llm.reply = "```json\n" + json.dumps(FULL_REPLY) + "\n```"
    assert social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))["title"] == "Ring size?!"


def test_missing_fields_fall_back_to_the_highlight(fake_llm):
    fake_llm.reply = json.dumps({"hashtags": ["x"]})
    copy = social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))
    assert copy["title"] == HIGHLIGHT["title"]
    assert copy["caption"] == HIGHLIGHT["hook_text"]


def test_a_reply_that_is_not_json_raises(fake_llm):
    fake_llm.reply = "I can't help with that."
    with pytest.raises(ValueError):
        social.generate_social_copy(HIGHLIGHT, _ai(fake_llm))


def test_trends_and_niche_flow_through_generation(fake_llm, tmp_path):
    fake_llm.reply = json.dumps(FULL_REPLY)
    loaded = _trends(tmp_path, "#dramaalert", age_days=1)
    social.generate_social_copy(HIGHLIGHT, _ai(fake_llm), niche="reality TV", trends=loaded)
    prompt = fake_llm.requests[0]["body"]["messages"][-1]["content"]
    assert "#dramaalert" in prompt and "Account niche: reality TV" in prompt


# --- helpers ---------------------------------------------------------------------------------


def test_caption_line_puts_hashtags_after_the_caption():
    assert social.caption_line({"caption": "Wild night", "hashtags": ["drama", "wtf"]}) == "Wild night #drama #wtf"
    assert social.caption_line({"caption": "Just text"}) == "Just text"


def test_save_and_load_round_trip(tmp_path):
    copy = {"title": "Café 😮", "caption": "c", "hashtags": ["x"], "variants": [{"style": "tease", "caption": "c"}]}
    path = social.save_social(tmp_path, 4, copy)
    assert path == tmp_path / "social" / "clip_004.json"
    assert social.load_social(tmp_path, 4) == copy


def test_loading_missing_or_corrupt_copy_gives_none(tmp_path):
    assert social.load_social(tmp_path, 0) is None
    social.save_social(tmp_path, 1, {"title": "x"})
    social.social_path(tmp_path, 1).write_text("{ broken", encoding="utf-8")
    assert social.load_social(tmp_path, 1) is None
