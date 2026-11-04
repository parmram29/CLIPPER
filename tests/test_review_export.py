import csv
import subprocess
import sys

import pytest

from clipper import evaluation, export, review, social

from conftest import highlight


@pytest.fixture
def session_dir(tmp_path):
    """A session with 3 picks: #1 and #3 rendered, #1 has post copy."""
    import json

    session = tmp_path / "sessions" / "20261104_182800"
    session.mkdir(parents=True)
    highlights = [
        {**highlight("00:26:56,000", "00:27:58,000", title="He said 'I was captivated'", score=9),
         "category": "drama", "window_score": 7, "duration_seconds": 62.0,
         "description": "a confession", "transcript_text": "I was captivated " * 40},
        {**highlight("00:33:04,000", "00:34:06,000", title="Ring size", score=8), "category": "funny", "duration_seconds": 62.0},
        {**highlight("01:02:00,000", "01:03:10,000", title="Café fight ⚡", score=6), "category": "argument", "duration_seconds": 70.0},
    ]
    (session / "session_data.json").write_text(json.dumps({"url": "https://youtu.be/x", "highlights": highlights}), encoding="utf-8")
    for i, payload in ((0, b"clip-one"), (2, b"clip-three")):
        folder = session / "clips" / f"clip_{i:03d}"
        folder.mkdir(parents=True)
        (folder / "master.mp4").write_bytes(payload)
    social.save_social(session, 0, {"title": "Captivated?!", "caption": "He could not look away", "hashtags": ["drama", "wtf"]})
    return session


# --- review sheet ---------------------------------------------------------------


def test_review_rows_describe_every_pick(session_dir):
    rows = review.build_rows(session_dir)
    assert [r["clip_no"] for r in rows] == [1, 2, 3]
    assert [r["rendered"] for r in rows] == ["yes", "no", "yes"]
    first = rows[0]
    assert first["title"] == "Captivated?!"  # post copy wins over the working title
    assert first["hashtags"] == "#drama #wtf" and first["caption"] == "He could not look away"
    assert (first["virality_score"], first["window_score"], first["category"]) == (9, 7, "drama")
    assert first["my_rating"] == "" and first["notes"] == ""  # yours to fill in
    assert len(first["transcript_excerpt"]) == 300  # long text is cut, so the sheet stays readable
    assert rows[1]["file"] == "" and rows[0]["file"].endswith("clip_000/master.mp4")
    assert rows[1]["title"] == "Ring size"  # no post copy yet, so the working title


def test_review_csv_round_trips_accents_and_has_the_expected_columns(session_dir):
    path = review.write_review_csv(session_dir)
    assert path == session_dir / "review.csv"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM, so Excel/Numbers read UTF-8
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert reader.fieldnames == review.COLUMNS
    assert rows[2]["title"] == "Café fight ⚡"


def test_a_session_with_no_picks_cannot_be_reviewed(tmp_path):
    import json

    (tmp_path / "session_data.json").write_text(json.dumps({"highlights": []}))
    with pytest.raises(ValueError, match="no highlights"):
        review.write_review_csv(tmp_path)


def test_a_missing_session_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="clipper find"):
        review.build_rows(tmp_path)


def test_rating_the_sheet_then_evaluating_it_closes_the_loop(session_dir):
    path = review.write_review_csv(session_dir)
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row, mine in zip(rows, ["9", "7", "3"]):  # I agree with the model's order
        row["my_rating"] = mine
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=review.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    report = evaluation.evaluate(evaluation.load_rated(path), good=7, k=2)
    assert report.n == 3
    assert report.spearman == pytest.approx(1.0)
    assert report.precision_at_k == 1.0


# --- export ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("He said 'I was captivated'", "he-said-i-was-captivated"),
        ("Café fight ⚡", "cafe-fight"),
        ("   ", "clip"),
        ("🔥🔥🔥", "clip"),
        ("a" * 100, "a" * 60),
        ("Hello --- World!!!", "hello-world"),
    ],
)
def test_slugify(text, expected):
    assert export.slugify(text) == expected


def test_export_copies_clips_with_readable_names_and_writes_captions_and_a_sheet(session_dir, tmp_path):
    dest = tmp_path / "to-phone"
    result = export.export_clips(session_dir, dest)

    assert [p.name for p in result.clips] == ["01 - captivated.mp4", "03 - cafe-fight.mp4"]
    assert (dest / "01 - captivated.mp4").read_bytes() == b"clip-one"  # the real file, not a stub
    assert (dest / "03 - cafe-fight.mp4").read_bytes() == b"clip-three"

    captions = result.captions.read_text(encoding="utf-8")
    assert "01 - captivated.mp4" in captions
    assert "Caption: He could not look away #drama #wtf" in captions  # copy-paste ready
    assert "Hook:    H" in captions
    # clip 3 has no post copy yet, so its caption falls back to the pick's own description
    third = captions.split("03 - cafe-fight.mp4")[1]
    assert "Title:   Café fight ⚡" in third and "Caption: d" in third
    assert result.review == dest / "review.csv" and result.review.is_file()
    # the originals are untouched
    assert (session_dir / "clips" / "clip_000" / "master.mp4").read_bytes() == b"clip-one"


def test_export_without_rendered_clips_says_what_to_run(tmp_path):
    import json

    (tmp_path / "session_data.json").write_text(json.dumps({"highlights": [{"title": "x"}]}))
    with pytest.raises(ValueError, match="clipper process"):
        export.export_clips(tmp_path, tmp_path / "out")


def test_the_default_folder_is_on_the_desktop_named_after_the_session(session_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(export.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    result = export.export_clips(session_dir)
    assert result.dest == tmp_path / "home" / "Desktop" / "clipper-exports" / "20261104_182800"
    assert len(list(result.dest.glob("*.mp4"))) == 2


def test_exporting_twice_overwrites_cleanly(session_dir, tmp_path):
    export.export_clips(session_dir, tmp_path / "d")
    export.export_clips(session_dir, tmp_path / "d")
    assert len(list((tmp_path / "d").glob("*.mp4"))) == 2


def test_a_tilde_destination_is_expanded(session_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = export.export_clips(session_dir, "~/phone-clips")
    assert result.dest == tmp_path / "phone-clips"


# --- opening the folder ----------------------------------------------------------


@pytest.mark.parametrize(
    "platform,expected",
    [("darwin", ["open", "/x"]), ("linux", ["xdg-open", "/x"])],
)
def test_reveal_uses_the_platforms_opener(monkeypatch, platform, expected):
    calls = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert export.reveal("/x") is True
    assert calls == [expected]


def test_reveal_does_nothing_on_unsupported_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert export.reveal("/x") is False


def test_reveal_reports_failure_instead_of_crashing(monkeypatch):
    def boom(*a, **k):
        raise OSError("no opener")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", boom)
    assert export.reveal("/x") is False
