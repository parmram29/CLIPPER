"""Phase 1 end to end (subtitles -> LLM -> session on disk), and the CLI commands
around it. Only the YouTube download is replaced by a local fixture; the real
session orchestration, SRT parsing, LLM call and file writing all run."""

import json
import sys
import types
from pathlib import Path

import pytest

from clipper import cli, session as session_mod

from conftest import highlight


def _write_srt(path: Path, seconds=400, step=5):
    lines = []
    for i, t in enumerate(range(0, seconds, step), 1):
        a = f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d},000"
        e = t + step - 1
        b = f"{e // 3600:02d}:{e % 3600 // 60:02d}:{e % 60:02d},000"
        lines.append(f"{i}\n{a} --> {b}\nspoken words at {t} seconds\n")
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def offline_youtube(monkeypatch):
    """Replace only the network fetches with local files."""

    def fake_subtitle_only(url, subtitle_language, temp_dir, cookies_path, log=None):
        srt = Path(temp_dir) / f"source.{subtitle_language}.srt"
        _write_srt(srt)
        return str(srt), {"title": "Fixture Show", "channel": "Fixture", "description": "d"}

    def fake_caption_words(url, temp_dir, cookies_path, log=None):
        words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
                 for i, w in enumerate("this is a fixture caption track".split())]
        path = Path(temp_dir) / "words.json"
        path.write_text(json.dumps(words), encoding="utf-8")
        return str(path)

    monkeypatch.setattr(session_mod, "download_subtitle_only", fake_subtitle_only)
    monkeypatch.setattr(session_mod, "download_caption_words", fake_caption_words)


def test_find_highlights_only_writes_a_complete_session(tmp_path, fake_llm, offline_youtube):
    fake_llm.reply = json.dumps(
        [
            highlight("00:00:10,000", "00:01:15,000", title="first", score=6),
            highlight("00:02:00,000", "00:03:30,000", title="second", score=9),
        ]
    )
    data = session_mod.find_highlights_only(
        url="https://www.youtube.com/watch?v=fixture",
        num_clips=2,
        subtitle_language="en",
        output_dir=tmp_path,
        cookies_path="unused",
        ai={"api_key": "k", "base_url": fake_llm.base_url, "model": "qwen-plus"},
        log=lambda m: None,
    )

    session_dir = Path(data["session_dir"])
    on_disk = json.loads((session_dir / "session_data.json").read_text(encoding="utf-8"))
    assert on_disk["url"] == "https://www.youtube.com/watch?v=fixture"
    assert on_disk["status"] == "highlights_found"
    assert on_disk["caption_available"] is True
    assert (session_dir / "words.json").is_file()
    assert [h["title"] for h in on_disk["highlights"]] == ["first", "second"]
    # transcript text for each highlight was extracted from the SRT
    assert "spoken words at 10 seconds" in on_disk["highlights"][0]["transcript_text"]
    assert on_disk["token_usage"] == {"prompt_tokens": 11, "completion_tokens": 22}

    # ...and the CLI can pick from it
    top = cli.select_highlights(cli.load_session(session_dir), top=1)
    assert [(h["title"], h["_highlight_index"]) for h in top] == [("second", 1)]


def test_no_subtitle_track_fails_loudly(tmp_path, fake_llm, monkeypatch):
    # Upstream has no speech-to-text fallback. Pin that behaviour so the gap is visible.
    monkeypatch.setattr(
        session_mod, "download_subtitle_only", lambda **kw: (None, {"title": "No Subs"})
    )
    with pytest.raises(RuntimeError, match="No subtitle available"):
        session_mod.find_highlights_only(
            url="https://youtu.be/x",
            num_clips=1,
            subtitle_language="en",
            output_dir=tmp_path,
            cookies_path="unused",
            ai={"api_key": "k", "base_url": fake_llm.base_url, "model": "m"},
            log=lambda m: None,
        )
    assert fake_llm.requests == []  # never reached the model


# --- CLI wiring -------------------------------------------------------------


@pytest.fixture
def cookies_file(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n.youtube.com\tTRUE\t/\tTRUE\t0\tHSID\tdef\n")
    return path


def test_cookies_validation_rejects_a_file_without_auth_cookies(tmp_path):
    empty = tmp_path / "cookies.txt"
    empty.write_text("# Netscape HTTP Cookie File\n")
    with pytest.raises(cli.config.ConfigError, match="missing YouTube authentication cookies"):
        cli.resolve_cookies(str(empty))


def test_resolved_cookies_are_exported_for_the_section_downloader(cookies_file, monkeypatch):
    from clipper.video_processor import _get_cookies_path

    monkeypatch.delenv("CLIPPER_COOKIES", raising=False)
    resolved = cli.resolve_cookies(str(cookies_file))
    assert _get_cookies_path() == resolved == str(cookies_file.resolve())


def test_process_command_passes_selection_and_options_through(tmp_path, cookies_file, monkeypatch, capsys):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "session_data.json").write_text(
        json.dumps(
            {
                "url": "https://youtu.be/abc",
                "highlights": [
                    highlight("00:00:10,000", "00:01:15,000", title="low", score=3),
                    highlight("00:02:00,000", "00:03:30,000", title="high", score=9),
                ],
            }
        )
    )

    captured = {}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return {"results": [{"output_path": str(session_dir / "clips" / "clip_001" / "master.mp4")}]}

    stub = types.ModuleType("clipper.clip_processor")
    stub.process_selected_highlights = fake_process
    monkeypatch.setitem(sys.modules, "clipper.clip_processor", stub)

    code = cli.main(
        [
            "process", str(session_dir), "--top", "1", "--cookies", str(cookies_file),
            "--reframe", "centered", "--background", "blurred", "--no-hook", "--tail-out", "4",
        ]
    )
    assert code == 0
    assert captured["url"] == "https://youtu.be/abc"
    assert [h["title"] for h in captured["highlights"]] == ["high"]
    assert captured["highlights"][0]["_highlight_index"] == 1
    assert captured["options"] == {
        "reframeMode": "centered", "centeredBackground": "blurred",
        "addCaptions": True, "addHook": False, "addWatermark": False, "addCreditWatermark": False,
    }
    assert captured["ai"] == {"clip_padding": {"lead_in": None, "tail_out": 4.0}}
    assert "master.mp4" in capsys.readouterr().out


def test_find_command_reports_config_errors_without_a_traceback(monkeypatch, capsys, tmp_path):
    for var in ("CLIPPER_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    code = cli.main(["find", "https://youtu.be/x"])
    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: No API key configured")
    assert "Traceback" not in err


def test_find_command_end_to_end_offline(tmp_path, fake_llm, offline_youtube, cookies_file, monkeypatch, capsys):
    monkeypatch.setenv("CLIPPER_API_KEY", "sk-test")
    monkeypatch.setenv("CLIPPER_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("CLIPPER_MODEL", "qwen-plus")
    monkeypatch.chdir(tmp_path)
    fake_llm.reply = json.dumps([highlight("00:00:10,000", "00:01:15,000", title="Great moment", score=8)])

    code = cli.main(["find", "https://youtu.be/x", "--clips", "1", "--cookies", str(cookies_file), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0
    assert "Great moment" in out and "Next:    clipper process" in out
    assert fake_llm.requests[0]["auth"] == "Bearer sk-test"
    assert list((tmp_path / "out" / "sessions").glob("*/session_data.json"))
