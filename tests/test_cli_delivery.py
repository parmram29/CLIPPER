"""The CLI around the new features: defaults, post copy, export, review, eval, and a long
video found end to end through `clipper find` (only the YouTube fetch is replaced)."""

import json
import sys
import types
from pathlib import Path

import pytest

from clipper import cli, session as session_mod

from conftest import highlight, make_transcript, write_srt
from test_windowing import windowed_reply


@pytest.fixture
def cookies_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n")
    return path


def _session(tmp_path):
    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)
    highlights = [
        {**highlight("00:10:00,000", "00:11:10,000", title="First", score=9), "category": "drama",
         "transcript_text": "you cannot say that to me"},
        {**highlight("00:20:00,000", "00:21:10,000", title="Second", score=5), "category": "funny",
         "transcript_text": "that was a terrible joke"},
    ]
    (session / "session_data.json").write_text(
        json.dumps({"url": "https://youtu.be/abc", "output_language_name": "English", "highlights": highlights})
    )
    return session


@pytest.fixture
def fake_renderer(monkeypatch):
    """Stands in for rendering: writes a master.mp4 for each clip it is asked for."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        session = Path(kwargs["session_dir"])
        results = []
        for h in kwargs["highlights"]:
            folder = session / "clips" / f"clip_{h['_highlight_index']:03d}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "master.mp4").write_bytes(b"video")
            results.append({"clip_index": h["_highlight_index"], "output_path": str(folder / "master.mp4")})
        return {"results": results}

    stub = types.ModuleType("clipper.clip_processor")
    stub.process_selected_highlights = fake
    monkeypatch.setitem(sys.modules, "clipper.clip_processor", stub)
    return calls


def _ai_env(monkeypatch, server):
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-test")
    monkeypatch.setenv("CLIPPER_BASE_URL", server.base_url)
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-flash")


# --- defaults ------------------------------------------------------------------------


def test_the_blurred_background_is_now_the_default():
    for command in (["process", "d"], ["run", "https://youtu.be/x"]):
        assert cli.build_parser().parse_args(command).background == "blurred"


def test_black_bars_are_still_available():
    assert cli.build_parser().parse_args(["process", "d", "--background", "black"]).background == "black"


def test_delivery_flags_exist_on_process_and_run():
    for command in (["process", "d"], ["run", "https://youtu.be/x"]):
        args = cli.build_parser().parse_args(command)
        assert (args.no_social, args.export, args.export_to, args.open) == (False, False, None, False)


def test_the_blurred_default_reaches_the_renderer(tmp_path, cookies_file, fake_renderer):
    session = _session(tmp_path)
    assert cli.main(["process", str(session), "--cookies", str(cookies_file), "--reframe", "centered", "--no-social"]) == 0
    assert fake_renderer[0]["options"]["centeredBackground"] == "blurred"


# --- post copy -----------------------------------------------------------------------


def test_process_writes_a_title_caption_and_hashtags_for_each_clip(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps({"title": "Do not say that", "caption": "She went there", "hashtags": ["Drama", "wtf"]})
    session = _session(tmp_path)

    assert cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "1"]) == 0

    copy = json.loads((session / "social" / "clip_000.json").read_text(encoding="utf-8"))
    # this fake model sends the old single-caption shape, which is still accepted
    assert (copy["title"], copy["caption"], copy["hashtags"], copy["platform"]) == (
        "Do not say that", "She went there", ["drama", "wtf"], "tiktok",
    )
    assert copy["variants"] == [{"style": "caption", "caption": "She went there"}]
    assert not (session / "social" / "clip_001.json").exists()  # only the clip that was rendered
    assert "She went there #drama #wtf" in capsys.readouterr().out
    prompt = fake_llm.requests[0]["body"]["messages"][-1]["content"]
    assert "you cannot say that to me" in prompt and "casual English" in prompt  # the session's language


def test_existing_copy_is_not_regenerated_unless_forced(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = json.dumps({"title": "T", "caption": "C", "hashtags": ["x"]})
    session = _session(tmp_path)
    args = ["process", str(session), "--cookies", str(cookies_file), "--top", "1"]
    cli.main(args)
    cli.main(args)
    assert len(fake_llm.requests) == 1
    cli.main(["social", str(session), "--top", "1", "--force"])
    assert len(fake_llm.requests) == 2


def test_no_social_makes_no_ai_call(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch):
    _ai_env(monkeypatch, fake_llm)
    session = _session(tmp_path)
    cli.main(["process", str(session), "--cookies", str(cookies_file), "--no-social"])
    assert fake_llm.requests == [] and not (session / "social").exists()


def test_a_missing_key_skips_post_copy_but_not_the_clips(tmp_path, cookies_file, fake_renderer, capsys):
    session = _session(tmp_path)
    assert cli.main(["process", str(session), "--cookies", str(cookies_file)]) == 0
    out = capsys.readouterr().out
    assert "Skipping titles and captions" in out
    assert (session / "clips" / "clip_000" / "master.mp4").is_file()  # the render still happened


def test_a_failing_caption_call_never_fails_the_run(tmp_path, cookies_file, fake_renderer, fake_llm, monkeypatch, capsys):
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply = "I can't do that"
    session = _session(tmp_path)
    assert cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "1"]) == 0
    assert "Could not write a caption for clip 1" in capsys.readouterr().out


def test_the_social_command_needs_a_key(tmp_path, capsys):
    session = _session(tmp_path)
    assert cli.main(["social", str(session)]) == 1
    assert "No API key configured" in capsys.readouterr().err


# --- export / review / eval -----------------------------------------------------------


def test_process_can_export_straight_to_a_folder(tmp_path, cookies_file, fake_renderer, capsys):
    session = _session(tmp_path)
    dest = tmp_path / "airdrop"
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--no-social", "--top", "1", "--export-to", str(dest)])
    out = capsys.readouterr().out
    assert code == 0
    assert (dest / "01 - first.mp4").read_bytes() == b"video"
    assert (dest / "captions.txt").is_file() and (dest / "review.csv").is_file()
    assert f"Exported 1 clip(s) to: {dest}" in out


def test_open_alone_implies_an_export_and_opens_the_folder(tmp_path, cookies_file, fake_renderer, monkeypatch):
    from clipper import export

    opened = []
    monkeypatch.setattr(export, "reveal", lambda path: opened.append(Path(path)) or True)
    monkeypatch.setattr(export.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = _session(tmp_path)
    cli.main(["process", str(session), "--cookies", str(cookies_file), "--no-social", "--top", "1", "--open"])
    assert opened == [tmp_path / "home" / "Desktop" / "clipper-exports" / "s1"]


def test_the_export_command(tmp_path, fake_renderer, capsys, monkeypatch):
    session = _session(tmp_path)
    (session / "clips" / "clip_000").mkdir(parents=True)
    (session / "clips" / "clip_000" / "master.mp4").write_bytes(b"v")
    dest = tmp_path / "out-folder"
    assert cli.main(["export", str(session), "--to", str(dest)]) == 0
    assert (dest / "01 - first.mp4").is_file()


def test_exporting_before_rendering_explains_itself(tmp_path, capsys):
    session = _session(tmp_path)
    assert cli.main(["export", str(session), "--to", str(tmp_path / "x")]) == 1
    assert "clipper process" in capsys.readouterr().err


def test_the_review_command_writes_the_sheet_and_explains_google_sheets(tmp_path, capsys):
    session = _session(tmp_path)
    assert cli.main(["review", str(session)]) == 0
    out = capsys.readouterr().out
    assert (session / "review.csv").is_file()
    assert "File > Import > Upload" in out and "my_rating" in out and "clipper eval" in out


def test_the_eval_command_prints_a_report(tmp_path, capsys):
    session = _session(tmp_path)
    cli.main(["review", str(session), "--out", str(tmp_path / "sheet.csv")])
    sheet = tmp_path / "sheet.csv"
    import csv

    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    from clipper import review

    for row, mine in zip(rows, ["8", "3"]):
        row["my_rating"] = mine
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=review.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    capsys.readouterr()

    assert cli.main(["eval", str(sheet), "--good", "7", "--k", "1"]) == 0
    out = capsys.readouterr().out
    assert "Rated clips: 2" in out and "Top 1 by model score: 100%" in out


# --- a long video through `clipper find` -----------------------------------------------


def test_find_on_a_long_video_uses_windows_the_viral_prompt_and_saves_the_categories(tmp_path, fake_llm, cookies_file, monkeypatch, capsys):
    def fake_subtitle_only(url, subtitle_language, temp_dir, cookies_path, log=None):
        srt = Path(temp_dir) / f"source.{subtitle_language}.srt"
        write_srt(srt, 100 * 60)
        return str(srt), {"title": "Six Hour Stream", "channel": "Chan", "description": "d"}

    monkeypatch.setattr(session_mod, "download_subtitle_only", fake_subtitle_only)
    monkeypatch.setattr(session_mod, "download_caption_words", lambda **kw: None)
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = windowed_reply()

    # --drama-share 0: this test is about windows, the prompt and the saved categories, so it runs the
    # plain pipeline (the drama check, on by default for this profile, has its own tests)
    code = cli.main(["find", "https://youtu.be/long", "--clips", "3", "--cookies", str(cookies_file),
                     "--out", str(tmp_path / "out"), "--drama-share", "0"])
    assert code == 0

    prompts = [r["body"]["messages"][0]["content"] for r in fake_llm.requests]
    assert len(prompts) == 5  # four windows and one re-scoring call
    assert all("what stops a thumb" in p for p in prompts[:-1])  # the viral prompt is the default
    assert "part 1/4" in prompts[0]

    (data_file,) = (tmp_path / "out" / "sessions").glob("*/session_data.json")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    assert [h["title"] for h in data["highlights"]] == ["late@0", "late@1680", "late@3360"]
    assert data["token_usage"] == {"prompt_tokens": 55, "completion_tokens": 110}
    assert "Highlights:" in capsys.readouterr().out


def test_no_rerank_and_window_flags_reach_the_session(tmp_path, fake_llm, cookies_file, monkeypatch):
    def fake_subtitle_only(url, subtitle_language, temp_dir, cookies_path, log=None):
        srt = Path(temp_dir) / f"source.{subtitle_language}.srt"
        write_srt(srt, 100 * 60)
        return str(srt), {"title": "T", "channel": "C", "description": "d"}

    monkeypatch.setattr(session_mod, "download_subtitle_only", fake_subtitle_only)
    monkeypatch.setattr(session_mod, "download_caption_words", lambda **kw: None)
    _ai_env(monkeypatch, fake_llm)
    fake_llm.reply_fn = windowed_reply()

    cli.main(["find", "https://youtu.be/long", "--clips", "3", "--cookies", str(cookies_file), "--out", str(tmp_path / "o"),
              "--window-minutes", "50", "--overlap-minutes", "5", "--no-rerank", "--drama-share", "0"])
    # 100 min in 50-minute windows with 5 minutes of overlap -> 3 windows, and no re-scoring call
    assert len(fake_llm.requests) == 3
    assert all("CANDIDATES:" not in r["body"]["messages"][0]["content"] for r in fake_llm.requests)
