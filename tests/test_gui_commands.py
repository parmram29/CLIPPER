import pytest

from clipper import cli
from clipper.gui import commands as c

VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize(
    "given",
    [
        CANON,
        f"{CANON}&list=PLxyz&t=30s&index=4",  # the playlist and the time are dropped
        f"https://youtu.be/{VID}?si=abc",
        f"youtu.be/{VID}",
        f"www.youtube.com/watch?v={VID}",  # no https:// typed
        f"https://m.youtube.com/watch?v={VID}&feature=share",
        f"https://music.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
        f"https://www.youtube.com/shorts/{VID}",
        f"   {CANON}   ",
    ],
)
def test_every_normal_way_of_pasting_a_video_becomes_the_same_address(given):
    assert c.normalize_youtube_url(given) == CANON


@pytest.mark.parametrize(
    "given",
    [
        "", "   ", None, "not a link", "https://vimeo.com/123456",
        f"https://evil.example/watch?v={VID}",
        f"https://youtube.com.evil.example/watch?v={VID}",  # looks like youtube at a glance
        f"https://youtube.com@evil.example/watch?v={VID}",  # the real host is after the @
        "https://www.youtube.com/playlist?list=PL123",  # a playlist is not one video
        "https://www.youtube.com/watch?v=short",
        f"https://www.youtube.com/watch?v={VID}x",
        "https://www.youtube.com/",
        "javascript:alert(1)",
        f"ftp://www.youtube.com/watch?v={VID}",
    ],
)
def test_anything_that_is_not_one_youtube_video_is_refused_with_a_sentence(given):
    with pytest.raises(c.RequestError) as caught:
        c.normalize_youtube_url(given)
    assert caught.value.args[0].endswith(".")


def test_text_is_one_tidy_line_with_a_limit():
    assert c.clean_text("  a\n\tb\x00c  ", 50) == "a b c"
    assert c.clean_text("x" * 600, 500) == "x" * 500
    assert c.clean_text(None, 10) == ""


def make_session(tmp_path, sid="20261105_130426"):
    folder = tmp_path / "out" / "sessions" / sid
    folder.mkdir(parents=True)
    (folder / "session_data.json").write_text("{}", encoding="utf-8")
    return folder


def test_only_real_session_folders_can_be_named(tmp_path):
    folder = make_session(tmp_path)
    assert c.session_path(tmp_path / "out", "20261105_130426") == folder
    for bad in ("../../etc", "20261105_13042", "2026110513042x", "", None, "20261105_130426/../x", "２０２６１１０５_１３０４２６"):  # last: full-width digits
        with pytest.raises(c.RequestError):
            c.session_path(tmp_path / "out", bad)
    with pytest.raises(c.RequestError, match="can't find"):
        c.session_path(tmp_path / "out", "20261106_000000")


def test_the_run_command_is_exactly_what_a_person_would_type(tmp_path):
    out = tmp_path / "out"
    argv = c.build_command("run", {"url": CANON, "style": "drama", "top": 3, "niche": "reality tv", "direction": "skip the intro"}, out)
    assert argv == ["run", CANON, "--clips=8", "--profile=viral", f"--out={out}", "--direction=skip the intro", "--top=3",
                    "--background=blurred", "--niche=reality tv", "--export"]


def test_each_style_maps_to_a_profile_and_the_drama_rule(tmp_path):
    out = tmp_path / "out"
    assert "--drama-share=0" in c.build_command("find", {"url": CANON, "style": "wild"}, out)  # funny/wild: no drama rule
    drama = c.build_command("find", {"url": CANON, "style": "drama"}, out)
    assert "--profile=viral" in drama and not any(a.startswith("--drama-share") for a in drama)
    assert "--profile=podcast" in c.build_command("find", {"url": CANON, "style": "podcast"}, out)
    assert c.build_command("find", {"url": CANON}, out)[3] == "--profile=viral"  # the default style
    with pytest.raises(c.RequestError):
        c.build_command("find", {"url": CANON, "style": "bogus"}, out)


def test_find_looks_for_enough_candidates_to_choose_from(tmp_path):
    out = tmp_path / "out"
    assert "--clips=8" in c.build_command("find", {"url": CANON}, out)
    assert "--clips=20" in c.build_command("find", {"url": CANON, "find": 20}, out)
    assert "--clips=10" in c.build_command("run", {"url": CANON, "top": 10, "find": 2}, out)  # never fewer than it must make


def test_process_and_export_need_a_real_session(tmp_path):
    folder = make_session(tmp_path)
    out = tmp_path / "out"
    argv = c.build_command("process", {"session": "20261105_130426", "picks": [3, 1, 3], "captions": False, "hook": False, "background": "black", "export": False}, out)
    assert argv == ["process", str(folder), "--pick=1,3", "--no-captions", "--no-hook", "--background=black"]
    assert c.build_command("export", {"session": "20261105_130426"}, out) == ["export", str(folder)]
    with pytest.raises(c.RequestError):
        c.build_command("process", {"session": "20261105_999999", "picks": [1]}, out)


@pytest.mark.parametrize(
    "params",
    [
        {"url": CANON, "top": 0}, {"url": CANON, "top": 11}, {"url": CANON, "top": "x"}, {"url": CANON, "top": True}, {"url": CANON, "top": [3]},
        {"url": CANON, "find": 21}, {"url": CANON, "captions": "yes"}, {"url": CANON, "background": "red"}, {"url": CANON, "export": 1},
    ],
)
def test_out_of_range_or_wrong_type_values_are_refused(tmp_path, params):
    with pytest.raises(c.RequestError):
        c.build_command("run", params, tmp_path / "out")


@pytest.mark.parametrize("picks", [None, [], "1,2", [0], [51], ["1"], [True], [1.5], [None]])
def test_picks_must_be_a_list_of_sensible_whole_numbers(tmp_path, picks):
    make_session(tmp_path)
    with pytest.raises(c.RequestError):
        c.build_command("process", {"session": "20261105_130426", "picks": picks}, tmp_path / "out")


def test_unknown_kinds_are_refused(tmp_path):
    for kind in ("", "rm", "doctor", None, "RUN"):
        with pytest.raises(c.RequestError):
            c.build_command(kind, {}, tmp_path)


# --- the contract with the real command line ------------------------------------------------------------


def parse(argv):
    return cli.build_parser().parse_args(argv)


def test_the_real_parser_reads_a_run_command_as_intended(tmp_path):
    args = parse(c.build_command("run", {"url": CANON, "style": "wild", "top": 4, "niche": "gta 6", "direction": "only the crashes",
                                        "captions": False, "hook": False, "background": "black"}, tmp_path / "out"))
    assert (args.command, args.url, args.top, args.profile, args.drama_share) == ("run", CANON, 4, "viral", "0")
    assert (args.niche, args.direction, args.background) == ("gta 6", "only the crashes", "black")
    assert args.no_captions and args.no_hook and args.export and args.clips == 8 and args.out == str(tmp_path / "out")


def test_the_real_parser_reads_find_process_and_export_as_intended(tmp_path):
    folder = make_session(tmp_path)
    out = tmp_path / "out"
    found = parse(c.build_command("find", {"url": CANON, "style": "podcast", "find": 12}, out))
    assert (found.command, found.profile, found.clips) == ("find", "podcast", 12) and found.drama_share is None
    made = parse(c.build_command("process", {"session": "20261105_130426", "picks": [2, 4]}, out))
    assert (made.command, made.session_dir, made.pick, made.top, made.export) == ("process", str(folder), "2,4", None, True)
    assert parse(c.build_command("export", {"session": "20261105_130426"}, out)).session_dir == str(folder)


@pytest.mark.parametrize("text", ["--help", "-h", "--top=99", "; rm -rf ~", "$(whoami)", "`id`", "'quoted'", '"double"'])
def test_odd_text_stays_text_and_never_becomes_an_option_or_a_command(tmp_path, text):
    argv = c.build_command("run", {"url": CANON, "direction": text, "niche": text}, tmp_path / "out")
    args = parse(argv)  # parse_args would exit on a real --help, or choke on a stray option
    assert args.direction == text and args.niche == text and args.top == 3
    assert not any(a.startswith(("--help", "-h")) for a in argv if not a.startswith("--direction=") and not a.startswith("--niche="))
