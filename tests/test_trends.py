import os
import time

import pytest

from clipper import trends


def _write(tmp_path, text, age_days=0.0, name="trends.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    when = time.time() - age_days * 86400
    os.utime(path, (when, when))
    return path


def test_hashtags_are_extracted_lowercased_in_order_without_duplicates():
    text = "Trending: #DramaAlert and #datingfails, again #dramaalert. Also #日本語 and #x_y"
    assert trends.extract_hashtags(text) == ["dramaalert", "datingfails", "日本語", "x_y"]


def test_a_plain_number_sign_is_not_a_hashtag():
    assert trends.extract_hashtags("rank # 1 and # 2") == []


def test_comment_lines_are_ignored_but_hashtag_lines_are_not(tmp_path):
    path = _write(tmp_path, "// instructions: #notatag\n#realtag\n  // indented comment #alsonot\nplain text\n")
    loaded = trends.load_trends(path)
    assert loaded.hashtags == ("realtag",)
    assert "instructions" not in loaded.text and "plain text" in loaded.text


def test_a_missing_or_empty_file_is_none(tmp_path):
    assert trends.load_trends(tmp_path / "nope.txt") is None
    assert trends.load_trends(_write(tmp_path, "// only comments\n\n")) is None


def test_age_is_taken_from_the_files_modification_time(tmp_path):
    loaded = trends.load_trends(_write(tmp_path, "#a", age_days=3))
    assert 2.99 < loaded.age_days < 3.01
    assert loaded.stale is False


def test_old_files_are_flagged_stale(tmp_path):
    assert trends.load_trends(_write(tmp_path, "#a", age_days=trends.STALE_AFTER_DAYS + 1)).stale is True
    assert trends.load_trends(_write(tmp_path, "#a", age_days=trends.STALE_AFTER_DAYS - 1)).stale is False


def test_the_staleness_limit_can_be_changed(tmp_path):
    path = _write(tmp_path, "#a", age_days=5)
    assert trends.load_trends(path, max_age_days=3).stale is True
    assert trends.load_trends(path, max_age_days=10).stale is False


def test_a_future_dated_file_does_not_give_a_negative_age(tmp_path):
    path = _write(tmp_path, "#a", age_days=-2)
    assert trends.load_trends(path).age_days == 0.0


def test_very_long_files_are_cut_on_a_line_boundary(tmp_path):
    lines = [f"#tag{i} line {i}" for i in range(500)]
    loaded = trends.load_trends(_write(tmp_path, "\n".join(lines)))
    assert loaded.truncated is True
    assert len(loaded.text) <= trends.MAX_CHARS
    kept = loaded.text.splitlines()
    assert kept[-1] == lines[len(kept) - 1]  # no half line at the end
    assert "tag499" not in loaded.hashtags


def test_the_template_round_trips_and_has_no_live_trends_in_it(tmp_path):
    created = trends.init_template(tmp_path / "t.txt")
    loaded = trends.load_trends(created)
    assert loaded is not None
    # the example hashtags are placeholders the user is told to replace
    assert set(loaded.hashtags) == {"examplehashtag", "anotherone"}
    assert "Creative Center" in created.read_text(encoding="utf-8")


def test_init_never_overwrites_an_existing_file(tmp_path):
    path = _write(tmp_path, "#mine")
    with pytest.raises(FileExistsError, match="already exists"):
        trends.init_template(path)
    assert path.read_text(encoding="utf-8") == "#mine"


def test_the_default_file_is_found_in_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "#here")
    assert trends.load_trends().hashtags == ("here",)


def test_describe_says_what_is_happening(tmp_path):
    nothing = trends.describe(None, "x.txt")
    assert "No trends available" in nothing and "clipper trends --refresh" in nothing and "clipper trends --init" in nothing
    fresh = trends.describe(trends.load_trends(_write(tmp_path, "#a #b", age_days=0.2)))
    assert "updated today" in fresh and "2 hashtag(s)" in fresh and "STALE" not in fresh
    stale = trends.describe(trends.load_trends(_write(tmp_path, "#a", age_days=40, name="old.txt")))
    assert "STALE" in stale and "40 day(s) ago" in stale
