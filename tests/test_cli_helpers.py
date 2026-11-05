import pytest

from clipper import cli


@pytest.mark.parametrize(
    "spec,total,expected",
    [
        ("1", 5, [0]),
        ("1,3", 5, [0, 2]),
        ("2-4", 5, [1, 2, 3]),
        ("1,3-4", 5, [0, 2, 3]),
        ("3,1", 5, [2, 0]),  # order is preserved
        ("1,1,2", 5, [0, 1]),  # duplicates dropped
        (" 2 , 4 ", 5, [1, 3]),
    ],
)
def test_parse_pick_valid(spec, total, expected):
    assert cli.parse_pick(spec, total) == expected


@pytest.mark.parametrize(
    "spec,total,message",
    [
        ("0", 5, "out of range"),
        ("6", 5, "out of range"),
        ("4-2", 5, "empty range"),
        ("a", 5, "cannot read"),
        ("1-x", 5, "cannot read"),
        ("", 5, "no clips selected"),
        (",", 5, "no clips selected"),
    ],
)
def test_parse_pick_invalid(spec, total, message):
    with pytest.raises(ValueError, match=message):
        cli.parse_pick(spec, total)


def test_top_indices_orders_by_score_and_keeps_ties_stable():
    hl = [{"virality_score": 5}, {"virality_score": 9}, {"virality_score": 9}, {"virality_score": 7}]
    assert cli.top_indices(hl, 3) == [1, 2, 3]
    assert cli.top_indices(hl, 10) == [1, 2, 3, 0]


def test_top_indices_tolerates_model_junk_scores():
    # The model sometimes returns a string, or no score at all.
    hl = [{"virality_score": "n/a"}, {"virality_score": "8"}, {}, {"virality_score": None}]
    assert cli.top_indices(hl, 1) == [1]
    assert cli.top_indices(hl, 4)[0] == 1


def test_top_must_be_positive():
    with pytest.raises(ValueError, match="at least 1"):
        cli.top_indices([{"virality_score": 1}], 0)


def _session():
    return {
        "url": "https://youtu.be/x",
        "highlights": [
            {"title": "a", "virality_score": 4},
            {"title": "b", "virality_score": 9},
            {"title": "c", "virality_score": 7},
        ],
    }


def test_select_defaults_to_all_and_tags_original_index():
    chosen = cli.select_highlights(_session())
    assert [h["_highlight_index"] for h in chosen] == [0, 1, 2]


def test_select_pick_keeps_original_index_for_stable_folders():
    chosen = cli.select_highlights(_session(), pick="3")
    assert [(h["title"], h["_highlight_index"]) for h in chosen] == [("c", 2)]


def test_select_top_is_best_first():
    chosen = cli.select_highlights(_session(), top=2)
    assert [h["title"] for h in chosen] == ["b", "c"]


def test_select_does_not_mutate_the_session():
    session = _session()
    cli.select_highlights(session, pick="1")
    assert "_highlight_index" not in session["highlights"][0]


def test_select_rejects_empty_session():
    with pytest.raises(ValueError, match="no highlights"):
        cli.select_highlights({"highlights": []})


def test_build_options_matches_what_upstream_reads():
    opts = cli.build_options(reframe="centered", background="blurred", captions=True, hook=False)
    assert opts == {
        "reframeMode": "centered",
        "centeredBackground": "blurred",
        "addCaptions": True,
        "addHook": False,
        "addWatermark": False,
        "addCreditWatermark": False,
    }


def test_prompt_file_requires_transcript_placeholder(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("Pick {num_clips} clips.\n{transcript}\n")
    assert "{transcript}" in cli.load_prompt_file(good)

    bad = tmp_path / "bad.txt"
    bad.write_text("Pick {num_clips} clips but forget the transcript")
    with pytest.raises(ValueError, match=r"\{transcript\}"):
        cli.load_prompt_file(bad)


def test_default_prompt_satisfies_our_own_placeholder_rule():
    from clipper.prompts import DEFAULT_HIGHLIGHT_PROMPT

    for placeholder in cli.REQUIRED_PROMPT_PLACEHOLDERS:
        assert placeholder in DEFAULT_HIGHLIGHT_PROMPT


def test_load_session_missing_file_is_a_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="clipper find"):
        cli.load_session(tmp_path)


def test_parser_wires_every_command():
    parser = cli.build_parser()
    assert parser.parse_args(["doctor"]).func is cli._cmd_doctor
    assert parser.parse_args(["find", "URL"]).func is cli._cmd_find
    assert parser.parse_args(["process", "dir", "--top", "2"]).func is cli._cmd_process
    assert parser.parse_args(["run", "URL"]).top == cli.DEFAULT_TOP


def test_process_rejects_pick_and_top_together():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["process", "dir", "--pick", "1", "--top", "2"])


def test_blur_background_uses_the_spelling_upstream_expects():
    # upstream's convert_to_portrait_centered only treats "blurred" as blur;
    # any other string silently falls back to black bars.
    args = cli.build_parser().parse_args(["process", "dir", "--background", "blurred"])
    assert args.background == "blurred"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["process", "dir", "--background", "blur"])


def test_the_help_says_this_is_built_on_yt_short_clipper(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "Built on top of yt-short-clipper" in out and "github.com/jipraks/yt-short-clipper" in out


def test_the_package_metadata_links_to_the_original_project():
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["urls"]["Upstream"] == "https://github.com/jipraks/yt-short-clipper"


def test_the_readme_credits_the_original_project_near_the_top():
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    top = readme[:1800]  # the intro, before any setup instructions
    assert "Built on top of an existing project" in top
    assert "github.com/jipraks/yt-short-clipper" in top and "MIT" in top
    # and the license file still carries the original author's notice, as the license requires
    license_text = (Path(__file__).resolve().parent.parent / "LICENSE").read_text(encoding="utf-8")
    assert "Aji Prakoso" in license_text
