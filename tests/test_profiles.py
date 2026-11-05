import json

import pytest

from clipper import cli, highlight_finder, profiles
from clipper.prompts import DEFAULT_HIGHLIGHT_PROMPT
from clipper.viral_prompt import CATEGORIES, VIRAL_HIGHLIGHT_PROMPT, VIRALITY_RUBRIC

from conftest import highlight


def test_the_viral_prompt_keeps_upstreams_placeholders():
    for placeholder in ("{num_clips}", "{user_direction}", "{output_language}", "{video_context}", "{transcript}"):
        assert placeholder in VIRAL_HIGHLIGHT_PROMPT
    assert "__RUBRIC__" not in VIRAL_HIGHLIGHT_PROMPT  # the rubric was substituted in
    assert VIRALITY_RUBRIC in VIRAL_HIGHLIGHT_PROMPT


@pytest.mark.parametrize(
    "wanted",
    [
        "drama", "arguments", "scandal", "wild", "outlandish", "wtf", "funny", "punchlines",
        "insults", "roasts", "spicy", "sneaky", "appropriate", "innuendo", "cliffhanger",
    ],
)
def test_the_viral_prompt_covers_everything_asked_for(wanted):
    assert wanted in VIRAL_HIGHLIGHT_PROMPT.lower()


def test_every_category_is_offered_to_the_model():
    for category in CATEGORIES:
        assert category in VIRAL_HIGHLIGHT_PROMPT


def test_the_viral_prompt_has_platform_safety_limits():
    lowered = VIRAL_HIGHLIGHT_PROMPT.lower()
    assert "slur" in lowered and "explicit" in lowered and "minors" in lowered
    assert "insults and roasts are fine" in lowered


def test_the_viral_prompt_does_not_invite_invented_speaker_names():
    # upstream's podcast prompt says the hook MUST name the speaker; on streams that invents names
    assert "never invent a name" in VIRAL_HIGHLIGHT_PROMPT.lower()
    assert "MUST name the person speaking" in DEFAULT_HIGHLIGHT_PROMPT  # the thing we avoided


def test_the_duration_rule_matches_what_upstream_enforces():
    # upstream's validator keeps only 58-120 s clips; the prompt must ask for a range inside that
    assert "60-120 seconds" in VIRAL_HIGHLIGHT_PROMPT


def test_profile_lookup():
    assert profiles.get_profile_prompt("viral") is VIRAL_HIGHLIGHT_PROMPT
    assert profiles.get_profile_prompt("podcast") is DEFAULT_HIGHLIGHT_PROMPT
    assert profiles.DEFAULT_PROFILE == "viral"
    with pytest.raises(ValueError, match="viral, podcast"):
        profiles.get_profile_prompt("nonsense")


@pytest.mark.parametrize("prompt_name", ["viral", "podcast"])
def test_a_direction_never_leaves_a_literal_output_language_placeholder(fake_llm, prompt_name):
    # upstream bug: the direction block was inserted after {output_language} had been replaced,
    # so the model saw the raw text "{output_language}" whenever --direction was used
    fake_llm.reply = json.dumps([highlight("00:10:00,000", "00:11:10,000")])
    highlight_finder.find_highlights(
        transcript="[00:10:00,000 - 00:10:04,000] hello",
        video_info={},
        num_clips=1,
        api_key="k",
        base_url=fake_llm.base_url,
        model="m",
        system_prompt=profiles.get_profile_prompt(prompt_name),
        user_direction="only the argument at the end",
        output_language="Spanish",
        log=lambda m: None,
    )
    prompt = fake_llm.requests[0]["body"]["messages"][0]["content"]
    assert "{output_language}" not in prompt
    # one sentence from the direction block, one from the reminder placed after the transcript
    assert "writing your output in Spanish." in prompt
    assert "title, description and hook_text are in Spanish." in prompt.split("FINAL CHECK ON THE USER'S DIRECTION")[1]


def test_a_transcript_containing_placeholder_text_is_left_alone(fake_llm):
    fake_llm.reply = json.dumps([highlight("00:10:00,000", "00:11:10,000")])
    highlight_finder.find_highlights(
        transcript="[00:10:00,000 - 00:10:04,000] the literal text {output_language} is spoken",
        video_info={},
        num_clips=1,
        api_key="k",
        base_url=fake_llm.base_url,
        model="m",
        user_direction="anything",
        output_language="Spanish",
        log=lambda m: None,
    )
    prompt = fake_llm.requests[0]["body"]["messages"][0]["content"]
    assert "the literal text {output_language} is spoken" in prompt  # not rewritten


def test_the_viral_prompt_works_through_upstreams_finder_and_keeps_the_category(fake_llm):
    reply = {**highlight("00:10:00,000", "00:11:10,000", title="He said 'get out'"), "category": "argument"}
    fake_llm.reply = json.dumps([reply])
    picks, _ = highlight_finder.find_highlights(
        transcript="[00:10:00,000 - 00:10:04,000] you need to leave",
        video_info={"title": "Show", "channel": "Chan", "description": "desc"},
        num_clips=2,
        api_key="k",
        base_url=fake_llm.base_url,
        model="qwen-flash",
        system_prompt=VIRAL_HIGHLIGHT_PROMPT,
        user_direction="skip the intro",
        log=lambda m: None,
    )

    prompt = fake_llm.requests[0]["body"]["messages"][0]["content"]
    for placeholder in ("{num_clips}", "{user_direction}", "{output_language}", "{video_context}", "{transcript}"):
        assert placeholder not in prompt, f"{placeholder} was not filled in"
    assert "you need to leave" in prompt and "skip the intro" in prompt and "Show" in prompt
    assert "EXACTLY 5 segments" in prompt  # 2 wanted + 3 spare, like upstream
    assert picks[0]["category"] == "argument"


# --- CLI wiring ----------------------------------------------------------------


def _find_args(*extra):
    return cli.build_parser().parse_args(["find", "https://youtu.be/x", *extra])


def test_the_defaults_are_the_viral_profile_with_half_hour_windows():
    args = _find_args()
    assert args.profile == "viral"
    assert (args.window_minutes, args.overlap_minutes, args.no_rerank) == (30, 2, False)
    settings = cli.prompt_and_window_settings(args)
    assert settings["system_message"] is VIRAL_HIGHLIGHT_PROMPT
    assert (settings["window_seconds"], settings["overlap_seconds"], settings["rerank"]) == (1800, 120, True)


def test_the_podcast_profile_selects_upstreams_prompt():
    settings = cli.prompt_and_window_settings(_find_args("--profile", "podcast"))
    assert settings["system_message"] is DEFAULT_HIGHLIGHT_PROMPT


def test_prompt_file_overrides_the_profile(tmp_path):
    prompt = tmp_path / "niche.txt"
    prompt.write_text("NICHE: find {num_clips} clips.\n{transcript}\n")
    settings = cli.prompt_and_window_settings(_find_args("--profile", "podcast", "--prompt-file", str(prompt)))
    assert settings["system_message"].startswith("NICHE:")


def test_window_options_are_converted_and_validated():
    settings = cli.prompt_and_window_settings(
        _find_args("--window-minutes", "20", "--overlap-minutes", "1.5", "--no-rerank")
    )
    assert (settings["window_seconds"], settings["overlap_seconds"], settings["rerank"]) == (1200, 90, False)
    for bad in (["--window-minutes", "5", "--overlap-minutes", "5"], ["--window-minutes", "0"], ["--overlap-minutes", "-1"]):
        with pytest.raises(ValueError, match="window-minutes"):
            cli.prompt_and_window_settings(_find_args(*bad))


def test_unknown_profiles_are_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        _find_args("--profile", "nonsense")


# --- the viral prompt must ask for conflict and an opening that is already the drama -----------------------------
# (the first real run came back with calm dating talk, and one pick was a plea to subscribe)


def test_the_viral_prompt_makes_conflict_or_shock_a_requirement_for_every_clip():
    assert "THE STOP-THE-THUMB TEST (NON-NEGOTIABLE)" in VIRAL_HIGHLIGHT_PROMPT
    for required in ("a confrontation, argument or call-out", "an accusation, an insult or a put-down",
                     "someone caught out, exposed or lying", "a shocking confession or reveal",
                     "a punchline or savage comeback that really lands"):
        assert required in VIRAL_HIGHLIGHT_PROMPT, required


def test_the_viral_prompt_says_calm_talk_is_not_a_clip_however_sweet():
    assert "is NOT a clip, however sweet or emotional it sounds" in VIRAL_HIGHLIGHT_PROMPT
    assert "Calm relationship talk: compliments, preferences" in VIRAL_HIGHLIGHT_PROMPT
    assert "never with filler" in VIRAL_HIGHLIGHT_PROMPT


def test_the_viral_prompt_asks_for_an_opening_that_is_already_the_hot_line():
    assert "OPEN ON THE HOT LINE" in VIRAL_HIGHLIGHT_PROMPT
    assert "hook_text must be that opening line" in VIRAL_HIGHLIGHT_PROMPT
    assert "Every clip passes the stop-the-thumb test and opens on its hottest line" in VIRAL_HIGHLIGHT_PROMPT


def test_the_viral_prompt_no_longer_offers_plain_flirting_or_tears_as_things_to_hunt_for():
    assert "SPICY but appropriate: flirting" not in VIRAL_HIGHLIGHT_PROMPT
    assert "only\n   when something happens" in VIRAL_HIGHLIGHT_PROMPT  # spicy needs an event
    assert "Tears alone, with no conflict, are not enough" in VIRAL_HIGHLIGHT_PROMPT
    assert "heartfelt confessions" not in VIRAL_HIGHLIGHT_PROMPT


def test_the_podcast_prompt_is_untouched_by_all_of_this():
    assert "STOP-THE-THUMB" not in DEFAULT_HIGHLIGHT_PROMPT
