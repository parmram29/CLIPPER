import json
import shutil
from pathlib import Path

import pytest

from clipper import export
from clipper.gui import sessions


@pytest.fixture
def real(gui_real_run, monkeypatch):
    base, home, _ = gui_real_run
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return base / "out", next((base / "out" / "sessions").iterdir()).name


def test_a_session_is_listed_with_what_a_card_needs(real):
    out, sid = real
    [card] = sessions.list_sessions(out)
    assert card["id"] == sid and card["title"] == "A very dramatic dinner" and card["channel"] == "Some Show"
    assert (card["picks"], card["made"]) == (4, 2) and card["url"].startswith("https://www.youtube.com/")
    assert card["created"].startswith("20") and "T" in card["created"]


def test_sessions_are_newest_first_and_junk_folders_are_skipped(real, tmp_path):
    out, sid = real
    sandbox = tmp_path / "out"
    shutil.copytree(out, sandbox)
    older = sandbox / "sessions" / "20200101_000000"
    shutil.copytree(sandbox / "sessions" / sid, older)
    (sandbox / "sessions" / "junk").mkdir()
    (sandbox / "sessions" / "20300101_000000").mkdir()  # no session_data.json: unfinished
    broken = sandbox / "sessions" / "20250101_000000"
    broken.mkdir()
    (broken / "session_data.json").write_text("{not json", encoding="utf-8")
    ids = [s["id"] for s in sessions.list_sessions(sandbox)]
    assert ids == [sid, "20200101_000000"]
    assert sessions.list_sessions(tmp_path / "nothing") == []


def test_one_session_has_everything_the_page_shows(real):
    out, sid = real
    data = sessions.read_session(out, sid)
    assert [p["number"] for p in data["picks"]] == [1, 2, 3, 4]
    assert data["ranking"] == [0, 1, 3, 2]  # drama scenes first (8, 7, 6), the calm chat last
    assert [p["rendered"] for p in data["picks"]] == [True, True, False, False]
    first = data["picks"][0]
    assert first["video"] == f"/media/{sid}/0" and data["picks"][2]["video"] is None
    assert (first["start"], first["end"], first["seconds"], first["drama"], first["virality"]) == ("00:10:02", "00:11:10", 68, 8, 9)
    assert data["drama"] == {"judged": 4, "scenes": 3, "total": 4}
    assert data["exported"] == 2  # the two clips copied to the Desktop folder
    assert data["video"] == {"title": "A very dramatic dinner", "channel": "Some Show"}


def test_post_copy_is_shown_and_marks_the_tags_that_came_from_live_trends(real):
    out, sid = real
    social = sessions.read_session(out, sid)["picks"][0]["social"]
    assert social["hashtags"] == ["drama", "realitytv", "fyp"]
    assert social["trend_tags"] == ["drama"]  # only this one was in the live trend sources
    assert [v["style"] for v in social["variants"]] == ["tease", "question", "hot take"]
    assert social["pinned_comment"] == "Team who?"
    assert sessions.read_session(out, sid)["picks"][2]["social"] is None  # not made, so no copy


def test_a_session_that_is_not_there_or_not_a_session_name_is_none(real):
    out, sid = real
    assert sessions.read_session(out, "20201231_235959") is None
    for bad in ("../x", "junk", "", "20261105_13042"):
        assert sessions.read_session(out, bad) is None


def test_clip_files_are_found_by_id_and_number_only(real):
    out, sid = real
    assert sessions.master_path(out, sid, 0).name == "master.mp4"
    assert sessions.master_path(out, sid, 2) is None  # not made
    for session, number in ((sid, -1), (sid, True), (sid, "0"), (sid, 1.0), ("../..", 0), ("20261105_130426/../..", 0), ("", 0)):
        assert sessions.master_path(out, session, number) is None


def test_odd_data_in_a_session_file_never_breaks_the_page(tmp_path):
    folder = tmp_path / "out" / "sessions" / "20261105_130426"
    folder.mkdir(parents=True)
    weird = {"url": 5, "video_info": "nope", "highlights": [{"title": None, "virality_score": "x", "duration_seconds": float("nan"), "drama_score": "high"}, "junk", 7]}
    (folder / "session_data.json").write_text(json.dumps(weird), encoding="utf-8")
    pick = sessions.read_session(tmp_path / "out", "20261105_130426")["picks"][0]
    assert (pick["title"], pick["virality"], pick["seconds"], pick["drama"]) == ("Untitled", None, None, None)
    assert sessions.list_sessions(tmp_path / "out")[0]["title"] == ""
