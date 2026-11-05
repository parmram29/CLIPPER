"""When one clip fails to render, the others still do, and the log says what is wrong.

From a real run: the first of three clips rendered, then the second clip's download died with
``FileNotFoundError: [Errno 2] No such file or directory`` from both of yt-dlp's HTTP handlers, and the
whole run ended there. The renderer is replaced by a stand-in that fails on cue.
"""

import json
import os
import sys
import types
from pathlib import Path

import pytest

from clipper import cli, diagnostics

from conftest import highlight
from test_cli_delivery import cookies_file  # noqa: F401 (fixture)

# The message from the real run, as it reached the CLI.
SETUP_ERROR = "Failed to download video section: Unable to handle request: 2 unexpected error(s)"
OTHER_ERROR = "Failed to burn captions: ffmpeg exploded"


def _session(tmp_path):
    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)
    highlights = [
        highlight("00:10:00,000", "00:11:10,000", title="One", score=9),
        highlight("00:20:00,000", "00:21:10,000", title="Two", score=8),
        highlight("00:30:00,000", "00:31:10,000", title="Three", score=7),
    ]
    (session / "session_data.json").write_text(
        json.dumps({"url": "https://youtu.be/abc", "output_language_name": "English", "highlights": highlights})
    )
    return session


class Renderer:
    """Stands in for rendering. ``plan`` maps a clip title to what happens on each attempt:
    an Exception to raise, or nothing (success). The renderer stops at the first failure, like the real one."""

    def __init__(self, plan=None):
        self.plan = plan or {}
        self.attempts = {}
        self.calls = []

    def __call__(self, **kwargs):
        batch = kwargs["highlights"]
        self.calls.append(
            {"titles": [h["title"] for h in batch], "verbose": os.environ.get("CLIPPER_YTDLP_VERBOSE")}
        )
        log = kwargs["log"]
        session = Path(kwargs["session_dir"])
        results = []
        for n, h in enumerate(batch, 1):
            log(f"Processing clip {n}/{len(batch)}: {h['title']}")
            folder = session / "clips" / f"clip_{h['_highlight_index']:03d}"
            master = folder / "master.mp4"
            if master.exists():
                results.append({"clip_index": h["_highlight_index"], "output_path": str(master), "skipped": True})
                continue
            self.attempts[h["title"]] = self.attempts.get(h["title"], 0) + 1
            outcomes = self.plan.get(h["title"], [])
            outcome = outcomes[self.attempts[h["title"]] - 1] if self.attempts[h["title"]] <= len(outcomes) else None
            if outcome is not None:
                raise outcome
            folder.mkdir(parents=True, exist_ok=True)
            master.write_bytes(b"video")
            log(f"[{n}/{len(batch)}] Clip saved: {master}")
            results.append({"clip_index": h["_highlight_index"], "output_path": str(master)})
        log(f"All {len(batch)} clips processed successfully!")
        return {"results": results}


@pytest.fixture
def install(monkeypatch):
    def put(renderer):
        stub = types.ModuleType("clipper.clip_processor")
        stub.process_selected_highlights = renderer
        monkeypatch.setitem(sys.modules, "clipper.clip_processor", stub)
        return renderer

    return put


def _run(tmp_path, cookies_file, *extra):
    session = _session(tmp_path)
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "3", "--no-social", *extra])
    return code, session


def _masters(session):
    return sorted(p.parent.name for p in (session / "clips").glob("*/master.mp4"))


# --- nothing goes wrong -----------------------------------------------------------------------------------


def test_a_clean_run_is_still_one_call_with_all_the_clips(tmp_path, cookies_file, install, capsys):
    renderer = install(Renderer())
    code, session = _run(tmp_path, cookies_file)
    assert code == 0
    assert [c["titles"] for c in renderer.calls] == [["One", "Two", "Three"]]
    assert _masters(session) == ["clip_000", "clip_001", "clip_002"]
    out = capsys.readouterr().out
    assert "Not rendered" not in out and "Diagnostics" not in out


# --- a failure in the download setup ------------------------------------------------------------------------


def test_a_setup_error_on_the_second_clip_is_retried_once_and_the_rest_still_render(tmp_path, cookies_file, install, capsys):
    renderer = install(Renderer({"Two": [RuntimeError(SETUP_ERROR)]}))
    code, session = _run(tmp_path, cookies_file)

    assert code == 0
    assert _masters(session) == ["clip_000", "clip_001", "clip_002"]
    # the batch stopped at Two; then Two alone (with the full debug output on), then Three alone
    assert [c["titles"] for c in renderer.calls] == [["One", "Two", "Three"], ["Two"], ["Three"]]
    assert [c["verbose"] for c in renderer.calls] == [None, "1", None]
    assert renderer.attempts == {"One": 1, "Two": 2, "Three": 1}  # the finished clip was not rendered again
    assert "CLIPPER_YTDLP_VERBOSE" not in os.environ  # switched off again afterwards

    out = capsys.readouterr().out
    assert "Rendering stopped: " + SETUP_ERROR in out
    assert "Diagnostics (include these lines if you ask for help):" in out
    assert "certifi file:" in out and "Trying that clip once more" in out
    assert "Processing clip 2/3: Two" in out and "Processing clip 3/3: Three" in out
    assert "Processing clip 1/1" not in out and "[1/1]" not in out  # the clips are numbered as a set
    assert "Not rendered" not in out


def test_a_setup_error_that_keeps_happening_is_reported_without_stopping_the_clips_after_it(tmp_path, cookies_file, install, capsys):
    renderer = install(Renderer({"Two": [RuntimeError(SETUP_ERROR)] * 5}))
    code, session = _run(tmp_path, cookies_file)

    assert code == 1
    assert _masters(session) == ["clip_000", "clip_002"]  # One, then Three; Two is the gap
    assert renderer.attempts == {"One": 1, "Two": 2, "Three": 1}  # one retry, not a loop
    out = capsys.readouterr().out
    assert "Clip 2/3 failed: " + SETUP_ERROR in out
    assert "Not rendered:" in out and "Two: " + SETUP_ERROR in out
    assert "Run the same command again to retry just these" in out
    assert "clip_000" in out and "clip_002" in out  # the clips that did render are still listed


def test_running_the_command_again_renders_only_what_is_missing(tmp_path, cookies_file, install):
    renderer = install(Renderer({"Two": [RuntimeError(SETUP_ERROR)] * 5}))
    code, session = _run(tmp_path, cookies_file)
    assert code == 1

    renderer.plan.clear()  # whatever was wrong is fixed
    renderer.calls.clear()
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "3", "--no-social"])
    assert code == 0
    assert _masters(session) == ["clip_000", "clip_001", "clip_002"]
    # Two: the batch, its retry, and now the re-run. One and Three were skipped, not redone.
    assert renderer.attempts == {"One": 1, "Two": 3, "Three": 1}


def test_the_hint_names_the_fix_when_the_certificate_file_is_really_missing(tmp_path, cookies_file, install, capsys, monkeypatch):
    install(Renderer({"Two": [RuntimeError(SETUP_ERROR)] * 5}))
    monkeypatch.setattr(diagnostics, "certifi_problem", lambda: "certifi's certificate file is missing: /x/cacert.pem")
    monkeypatch.setattr(diagnostics, "repair_certifi", lambda: False)
    _run(tmp_path, cookies_file)
    out = capsys.readouterr().out
    assert "certifi's certificate file is missing: /x/cacert.pem" in out
    assert "pip install --force-reinstall certifi" in out
    assert "had been forgotten in memory" not in out


def test_a_certificate_location_that_was_only_stale_in_memory_is_repaired_before_the_retry(tmp_path, cookies_file, install, capsys, monkeypatch):
    install(Renderer({"Two": [RuntimeError(SETUP_ERROR)]}))
    state = {"stale": True}
    monkeypatch.setattr(diagnostics, "certifi_problem", lambda: "stale" if state["stale"] else None)
    monkeypatch.setattr(diagnostics, "repair_certifi", lambda: state.update(stale=False) or True)
    code, _ = _run(tmp_path, cookies_file)
    assert code == 0
    assert "had been forgotten in memory; it has been found again" in capsys.readouterr().out


# --- a failure that is not about the download setup ----------------------------------------------------------


def test_an_error_that_is_not_about_the_download_setup_is_not_retried_but_the_rest_still_render(tmp_path, cookies_file, install, capsys):
    renderer = install(Renderer({"Two": [RuntimeError(OTHER_ERROR)] * 5}))
    code, session = _run(tmp_path, cookies_file)

    assert code == 1
    assert _masters(session) == ["clip_000", "clip_002"]
    assert renderer.attempts["Two"] == 1  # it would only fail the same way again, so it is not run twice
    out = capsys.readouterr().out
    assert "Diagnostics" not in out  # nothing to suggest about the download setup
    assert "Two: " + OTHER_ERROR in out


def test_if_the_first_clip_fails_the_others_are_still_tried(tmp_path, cookies_file, install):
    renderer = install(Renderer({"One": [RuntimeError(OTHER_ERROR)] * 5}))
    code, session = _run(tmp_path, cookies_file)
    assert code == 1 and _masters(session) == ["clip_001", "clip_002"]
    assert renderer.attempts == {"One": 1, "Two": 1, "Three": 1}


# --- what is done with the clips that exist --------------------------------------------------------------------


def test_titles_and_captions_are_only_written_for_clips_that_were_rendered(tmp_path, cookies_file, install, monkeypatch):
    install(Renderer({"Two": [RuntimeError(OTHER_ERROR)] * 5}))
    seen = []
    monkeypatch.setattr(cli, "_generate_social", lambda session_dir, session, chosen, **kw: seen.append([h["title"] for h in chosen]) or len(chosen))
    session = _session(tmp_path)
    code = cli.main(["process", str(session), "--cookies", str(cookies_file), "--top", "3"])
    assert code == 1
    assert seen == [["One", "Three"]]


def test_the_run_command_also_reports_a_failure_in_its_exit_status(tmp_path, cookies_file, install, monkeypatch):
    install(Renderer({"Two": [RuntimeError(OTHER_ERROR)] * 5}))
    session = _session(tmp_path)
    monkeypatch.setattr(cli, "_find", lambda args: {"session_dir": str(session), "url": "https://youtu.be/abc"})
    code = cli.main(["run", "https://youtu.be/abc", "--cookies", str(cookies_file), "--top", "3", "--no-social"])
    assert code == 1


# --- the yt-dlp debug switch -------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [(None, False), ("1", True)])
def test_the_environment_variable_turns_on_yt_dlps_debug_output(monkeypatch, tmp_path, value, expected):
    from clipper import video_processor

    seen = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            return 0

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(video_processor, "_setup_ytdlp_env", lambda: None)
    monkeypatch.setattr(video_processor, "get_ffmpeg_path", lambda: sys.executable)
    monkeypatch.setattr(video_processor, "get_deno_path", lambda: None)
    monkeypatch.setattr(video_processor, "_get_cookies_path", lambda: str(tmp_path / "cookies.txt"))
    monkeypatch.setattr(video_processor, "_find_downloaded_file", lambda p: p)
    if value is None:
        monkeypatch.delenv("CLIPPER_YTDLP_VERBOSE", raising=False)
    else:
        monkeypatch.setenv("CLIPPER_YTDLP_VERBOSE", value)

    video_processor._download_section_module("https://youtu.be/abc", "00:00:10", "00:01:10", str(tmp_path / "s.mp4"), lambda m: None)
    assert seen["verbose"] is expected
