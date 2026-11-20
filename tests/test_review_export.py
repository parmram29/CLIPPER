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


# --- caption angles, pinned comment, keywords and trend tags -------------------------------------------


def _rich_social(session_dir):
    social.save_social(
        session_dir,
        2,
        {
            "title": "Fight night",
            "caption": "He walked in and everyone went quiet",
            "variants": [
                {"style": "tease", "caption": "He walked in and everyone went quiet"},
                {"style": "question", "caption": "Would you have stayed?"},
                {"style": "hot take", "caption": "He was right and you know it"},
            ],
            "hashtags": ["drama", "dramaalert"],
            "keywords": ["reality tv fight", "awkward dinner"],
            "pinned_comment": "Team who?",
            "trend_tags_used": ["dramaalert"],
        },
    )


def test_the_review_sheet_carries_the_alternate_captions_and_the_rest(session_dir):
    _rich_social(session_dir)
    third = review.build_rows(session_dir)[2]
    assert third["caption"] == "He walked in and everyone went quiet"
    assert third["caption_alt"] == "Would you have stayed? || He was right and you know it"
    assert third["pinned_comment"] == "Team who?"
    assert third["keywords"] == "reality tv fight, awkward dinner"
    assert third["trend_tags"] == "#dramaalert"
    # a pick with no post copy yet leaves them blank rather than failing
    second = review.build_rows(session_dir)[1]
    assert (second["caption_alt"], second["pinned_comment"], second["keywords"], second["trend_tags"]) == ("", "", "", "")


def test_old_post_copy_without_the_new_fields_still_works(session_dir):
    # the first clip's copy was saved in the earlier shape (no variants, keywords or pinned comment)
    first = review.build_rows(session_dir)[0]
    assert first["caption"] == "He could not look away" and first["caption_alt"] == "" and first["pinned_comment"] == ""


def test_export_lists_every_caption_angle_with_the_hashtags_ready_to_paste(session_dir, tmp_path):
    _rich_social(session_dir)
    captions = export.export_clips(session_dir, tmp_path / "d").captions.read_text(encoding="utf-8")
    block = captions.split("03 - fight-night.mp4")[1]
    assert "Caption: He walked in and everyone went quiet #drama #dramaalert" in block
    assert "Alt (question): Would you have stayed? #drama #dramaalert" in block
    assert "Alt (hot take): He was right and you know it #drama #dramaalert" in block
    assert "Pinned comment: Team who?" in block
    assert "Search keywords: reality tv fight, awkward dinner" in block


# --- a matching text file beside every exported clip ---------------------------------------------


def test_each_exported_clip_gets_a_matching_text_file_with_its_own_hashtags(session_dir, tmp_path):
    _rich_social(session_dir)
    result = export.export_clips(session_dir, tmp_path / "d")

    assert [n.name for n in result.notes] == ["01 - captivated.txt", "03 - fight-night.txt"]
    # same folder and same name as the video, so the pair is obvious in Finder
    for clip, note in zip(result.clips, result.notes):
        assert note.parent == clip.parent and note.stem == clip.stem and note.is_file()

    first, third = (n.read_text(encoding="utf-8") for n in result.notes)
    assert first.startswith("01 - captivated.mp4\n")
    assert "Hashtags: #drama #wtf" in first
    assert "Hashtags: #drama #dramaalert" in third
    # nothing leaks between clips: each file only has its own clip's tags and words
    assert "dramaalert" not in first and "Fight night" not in first
    assert "#wtf" not in third and "Captivated" not in third


def test_a_clips_text_file_is_exactly_its_entry_in_captions_txt(session_dir, tmp_path):
    _rich_social(session_dir)
    result = export.export_clips(session_dir, tmp_path / "d")
    captions = result.captions.read_text(encoding="utf-8")
    expected = "\n\n".join(n.read_text(encoding="utf-8").rstrip("\n") for n in result.notes) + "\n"
    assert captions == expected


def test_a_clip_without_post_copy_still_gets_a_text_file_that_says_how_to_make_one(session_dir, tmp_path):
    result = export.export_clips(session_dir, tmp_path / "d")  # clip 3 has no post copy in the base fixture
    third = result.notes[1].read_text(encoding="utf-8")
    assert "Hashtags:" not in third  # there are none to show, and it must not pretend
    assert f"clipper social {session_dir}" in third
    assert "Caption: d" in third  # the pick's own description stands in


def test_the_text_file_says_where_in_the_source_video_the_clip_came_from(session_dir, tmp_path):
    import json

    data_file = session_dir / "session_data.json"
    data = json.loads(data_file.read_text(encoding="utf-8"))
    data["video_info"] = {"channel": "Some Streamer"}
    data_file.write_text(json.dumps(data), encoding="utf-8")

    result = export.export_clips(session_dir, tmp_path / "d")
    first, third = (n.read_text(encoding="utf-8") for n in result.notes)
    assert "Source:  https://youtu.be/x at 00:26:56 - 00:27:58 (channel: Some Streamer)" in first
    assert "Source:  https://youtu.be/x at 01:02:00 - 01:03:10 (channel: Some Streamer)" in third


def test_the_source_line_is_left_out_when_nothing_about_it_is_known():
    assert export._source_line({}, {}) == ""
    assert export._source_line({"url": "https://youtu.be/x"}, {}) == "Source:  https://youtu.be/x"


def test_exporting_twice_keeps_one_text_file_per_clip(session_dir, tmp_path):
    export.export_clips(session_dir, tmp_path / "d")
    export.export_clips(session_dir, tmp_path / "d")
    assert sorted(p.name for p in (tmp_path / "d").glob("*.txt")) == [
        "01 - captivated.txt",
        "03 - cafe-fight.txt",
        "captions.txt",
    ]


def test_the_how_to_hint_quotes_a_session_path_that_has_spaces(session_dir, tmp_path):
    import shutil

    moved = tmp_path / "my sessions" / "s 1"
    shutil.copytree(session_dir, moved)
    note = export.export_clips(moved, tmp_path / "d").notes[1].read_text(encoding="utf-8")
    assert f"clipper social '{moved}'" in note  # pasted into a terminal as-is, it must still work
