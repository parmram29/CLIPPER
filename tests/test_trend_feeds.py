import json
import time

import pytest

from clipper import cli, trend_feeds as feeds, trend_research, trends as trends_mod
from clipper.trend_net import FeedError

NOW = 1_763_553_600.0  # 2026-11-19 12:00 UTC
HOUR = 3600

GOOGLE = """<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><title>x</title>
<item><title>Love Is Blind</title><ht:approx_traffic>200K+</ht:approx_traffic></item>
<item><title>Weather Today</title><ht:approx_traffic>100K+</ht:approx_traffic></item></channel></rss>"""
WIKI = json.dumps({"items": [{"articles": [{"article": "Main_Page"}, {"article": "Love_Is_Blind_(TV_series)"}]}]})
BLUESKY = json.dumps({"topics": [{"topic": "Love Is Blind"}, {"topic": "Government shutdown"}]})
MASTODON = json.dumps([{"name": "caturday"}, {"name": "loveisblind"}])
NEWS = """<rss><channel><title>t</title>
<item><title>Love Is Blind reunion: the fight everyone is talking about - Variety</title></item>
<item><title>Love Is Blind season 9 reunion date - Deadline</title></item></channel></rss>"""
SUGGEST = json.dumps(["love is blind", ["love is blind reunion", "love is blind season 9 cast"]])
PAGE = "<html><body><ul><li>#DramaAlert is trending</li><li>#reunion</li></ul></body></html>"
VIDEO = {"title": "Love Is Blind Season 9 | Reunion | Netflix", "tags": ["love is blind", "reunion"], "channel": "Netflix"}

ANSWERS = [
    ("trends.google.com/trending/rss", GOOGLE),
    ("wikimedia.org", WIKI),
    ("bsky.app", BLUESKY),
    ("mastodon.social", MASTODON),
    ("news.google.com", NEWS),
    ("bing.com/news", NEWS),
    ("suggestqueries.google.com", SUGGEST),
    ("api.bing.com", SUGGEST),
    ("variety.com", NEWS), ("deadline.com", NEWS), ("tmz.com", NEWS), ("knowyourmeme.com", NEWS),
]


class Net:
    """A fake internet: answers by address, records every request, and can be made to fail."""

    def __init__(self, extra=(), fail=None, slow=None):
        self.answers = [*extra, *ANSWERS]
        self.calls = []
        self.fail = fail  # an exception raised for every request
        self.slow = slow or {}  # address fragment -> seconds to wait

    def __call__(self, url):
        self.calls.append(url)
        for fragment, seconds in self.slow.items():
            if fragment in url:
                time.sleep(seconds)
        if self.fail:
            raise self.fail
        for fragment, answer in self.answers:
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise FeedError("HTTP 404", status=404)


def youtube(seed, count):
    return [{"title": "Reunion went off #LoveIsBlind #drama", "view_count": 5000}]


def no_youtube(seed, count):
    raise FeedError("youtube down")


def read(tmp_path, net=None, ytdlp=youtube, **kw):
    net = net or Net()
    logs = []
    kw.setdefault("niche", "reality tv")
    kw.setdefault("region", "United States")
    kw.setdefault("now", NOW)
    result = feeds.auto_feeds(cache_dir=tmp_path, fetch=net, log=logs.append, ytdlp_search=ytdlp, env={}, sites_path=tmp_path / "none.txt", **kw)
    return result, net, logs


def test_a_full_read_ranks_what_sources_agree_on_and_ties_it_to_the_video(tmp_path):
    result, net, logs = read(tmp_path, video_info=VIDEO)
    assert result is not None and result.source == "feeds" and result.verified
    first_hot = next(line for line in result.text.splitlines() if line.startswith("Hot right now AND tied"))
    assert first_hot.startswith("Hot right now AND tied to your topic or this video: Love Is Blind [")
    assert "Google Trends" in first_hot and "Bluesky" in first_hot and "Google News" in first_hot
    assert "NOT TikTok's own data" in result.text
    assert "Tags on the source video itself: #loveisblind #reunion" in result.text
    assert "loveisblind" in result.hashtags and "reunion" in result.hashtags
    assert "caturday" not in result.hashtags  # a general source's one-off tag is not for this video
    assert "love is blind reunion" in result.text  # what people are searching for
    # the topic-based sources were asked about the VIDEO (cleaned title), then the niche
    assert any("q=Love+Is+Blind+Season+9+when%3A7d" in u for u in net.calls)
    assert any("q=reality+tv+when%3A7d" in u for u in net.calls)
    assert any("Google Trends: 2 names" in line for line in logs)


def test_the_result_is_reused_for_six_hours_then_read_again(tmp_path):
    first, net, _ = read(tmp_path, video_info=VIDEO)
    calls = len(net.calls)
    again, _, _ = read(tmp_path, net, video_info=VIDEO, now=NOW + 5 * HOUR)
    assert len(net.calls) == calls and again.text == first.text  # nothing was asked again
    read(tmp_path, net, video_info=VIDEO, now=NOW + 7 * HOUR)
    assert len(net.calls) > calls  # stale, so it was read afresh on its own
    after = len(net.calls)
    read(tmp_path, net, video_info=VIDEO, now=NOW + 7 * HOUR + 60, force=True)
    assert len(net.calls) > after  # force always reads


def test_each_video_has_its_own_saved_result(tmp_path):
    _, net, _ = read(tmp_path, video_info=VIDEO)
    calls = len(net.calls)
    read(tmp_path, net, video_info={"title": "A different show entirely"})
    assert len(net.calls) > calls


def test_when_every_source_fails_it_says_so_and_waits_before_trying_again(tmp_path):
    net = Net(fail=FeedError("couldn't connect (no network)"))
    logs = []
    kw = dict(niche="reality tv", region=None, cache_dir=tmp_path, fetch=net, log=logs.append, ytdlp_search=no_youtube, env={}, sites_path=tmp_path / "x.txt")
    assert feeds.auto_feeds(now=NOW, **kw) is None
    assert any("FAILED, couldn't connect" in line for line in logs)
    calls = len(net.calls)
    assert feeds.auto_feeds(now=NOW + 30 * 60, **kw) is None
    assert len(net.calls) == calls  # within the hour: not retried
    assert any("trying again within 1h" in line for line in logs)
    assert feeds.auto_feeds(now=NOW + 61 * 60, **kw) is None
    assert len(net.calls) > calls  # after the hour: tries again


def test_it_falls_back_to_the_last_saved_result_when_a_refresh_finds_nothing(tmp_path):
    first, _, _ = read(tmp_path)
    dead = Net(fail=FeedError("offline"))
    result, _, logs = read(tmp_path, dead, ytdlp=no_youtube, now=NOW + 8 * HOUR)
    assert result is not None and result.text == first.text
    assert any("Using the last saved live trends" in line for line in logs)


def test_a_refresh_that_got_far_less_than_last_time_does_not_replace_a_good_saved_result(tmp_path):
    first, _, _ = read(tmp_path)
    thin, _, logs = read(tmp_path, Net(fail=FeedError("offline")), now=NOW + 8 * HOUR)  # only the YouTube source answers
    assert thin.text == first.text
    assert any("Only 1 source(s) answered this time against" in line and "being kept" in line for line in logs)
    much_later, _, _ = read(tmp_path, Net(fail=FeedError("offline")), now=NOW + 30 * HOUR)
    assert "Government shutdown" not in much_later.text and much_later.text != first.text  # old enough: the thin one is better than stale


def test_one_failing_source_does_not_stop_the_others(tmp_path):
    net = Net(extra=[("trends.google.com/trending/rss", FeedError("the site answered HTTP 503", status=503))])
    result, _, logs = read(tmp_path, net, video_info=VIDEO)
    assert result is not None
    assert any(line.strip() == "Google Trends: FAILED, the site answered HTTP 503" for line in logs)
    assert "Bluesky" in result.text or "Wikipedia" in result.text  # still ranked from what did answer


def test_nothing_a_source_does_can_raise_out_of_the_module(tmp_path):
    result, _, logs = read(tmp_path, Net(fail=ValueError("boom")), ytdlp=no_youtube)
    assert result is None  # every source blew up in a way nobody planned for, and still nothing escaped
    assert any("FAILED, unexpected problem (boom)" in line for line in logs)


def test_the_sources_setting_limits_what_is_asked(tmp_path):
    logs, net = [], Net()
    feeds.auto_feeds(niche="reality tv", now=NOW, cache_dir=tmp_path, fetch=net, log=logs.append, ytdlp_search=youtube,
                     env={"CLIPPER_TREND_SOURCES": "google_trends"}, sites_path=tmp_path / "x.txt")
    assert len(net.calls) == 1 and "trends.google.com" in net.calls[0]


def test_websites_you_add_are_read_politely_and_only_add_tags_and_support(tmp_path):
    sites = tmp_path / "trend_sites.txt"
    sites.write_text(
        "https://good.example.com/hot | Good trends\n"
        "https://blocked.example.com/hot | Blocked trends\n"
        "https://search.example.net/?q={query} | Search site\n"
        "http://nope.example.com/\n",
        encoding="utf-8",
    )
    net = Net(extra=[
        ("good.example.com/robots.txt", "User-agent: *\nAllow: /"),
        ("good.example.com/hot", PAGE),
        ("blocked.example.com/robots.txt", "User-agent: *\nDisallow: /"),
        ("search.example.net/robots.txt", FeedError("HTTP 404", status=404)),
        ("search.example.net/?q=Love+Is+Blind+Season+9", PAGE),
    ])
    logs = []
    result = feeds.auto_feeds(niche="reality tv", video_info=VIDEO, now=NOW, cache_dir=tmp_path, fetch=net, log=logs.append,
                              ytdlp_search=youtube, env={}, sites_path=sites)
    assert "dramaalert" in result.hashtags  # a site's tags are offered
    joined = "\n".join(logs)
    assert "Blocked trends: skipped: the site's robots.txt asks robots not to read this page" in joined
    assert not any("blocked.example.com/hot" in u for u in net.calls)  # never even requested
    assert "Search site: " in joined and any("search.example.net/?q=Love+Is+Blind+Season+9" in u for u in net.calls)
    assert "trend_sites.txt: line 4" in joined  # the unusable line is reported, not ignored silently
    # a site cannot make something up and call it hot on its own
    assert "DramaAlert" not in result.text.split("Tags in use")[0]


def test_a_source_that_takes_too_long_is_given_up_on_without_holding_up_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(feeds, "TOTAL_SECONDS", 0.4)
    started = time.time()
    result, _, logs = read(tmp_path, Net(slow={"bsky.app": 3}), video_info=VIDEO)
    assert time.time() - started < 2.5
    assert result is not None
    assert any(line.strip() == "Bluesky trending: FAILED, took too long" for line in logs)


def test_status_reports_age_tags_hot_things_and_the_last_failure(tmp_path):
    empty = feeds.status("reality tv", "United States", now=NOW, cache_dir=tmp_path)
    assert empty["updated"] is None and empty["due"] and empty["sources"] == 0
    read(tmp_path)  # niche-level result, no video
    status = feeds.status("reality tv", "United States", now=NOW + 2 * HOUR, cache_dir=tmp_path)
    assert status["age_hours"] == 2.0 and not status["due"] and status["sources"] >= 5
    assert "loveisblind" in status["tags"] and any("Love Is Blind" in h for h in status["hot"])
    assert any("Google Trends" in n for n in status["notes"])
    assert feeds.status("reality tv", "United States", now=NOW + 7 * HOUR, cache_dir=tmp_path)["due"]
    read(tmp_path, Net(fail=FeedError("offline")), now=NOW + 8 * HOUR)  # a failed refresh leaves a marker
    assert feeds.status("reality tv", "United States", now=NOW + 8 * HOUR + 60, cache_dir=tmp_path)["failure"]


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Love Is Blind Season 9 | Reunion | Netflix", "Love Is Blind Season 9"),
        ("GTA 6 Gameplay (Official 4K) #gta6", "GTA 6 Gameplay"),
        ("The Wildest Dinner Ever - Full Episode", "The Wildest Dinner Ever"),
        ("one two three four five six seven eight nine", "one two three four five six seven"),
        ("", ""),
        (None, ""),
    ],
)
def test_the_video_title_is_cleaned_down_to_what_names_the_thing(title, expected):
    assert feeds.clean_title(title) == expected


def test_seeds_are_the_video_then_the_niche_without_repeats():
    assert feeds.video_seeds(VIDEO, "reality tv") == ("Love Is Blind Season 9", "reality tv")
    assert feeds.video_seeds(None, "reality tv") == ("reality tv",)
    assert feeds.video_seeds(VIDEO, "love is blind season 9") == ("Love Is Blind Season 9",)
    assert feeds.video_seeds(None, None) == ()


def test_video_tags_come_from_its_tags_title_and_description_as_hashtag_words():
    items = feeds.video_items({"title": "Fight #DramaAlert", "description": "More #Reunion2026 here", "tags": ["Love Is Blind", "x", 5]})
    assert items.tags == ["loveisblind", "dramaalert", "reunion2026"]
    assert feeds.video_items(None).is_empty()


def test_describe_says_plainly_these_are_not_tiktoks_own_data(tmp_path):
    result, _, _ = read(tmp_path)
    line = trends_mod.describe(result)
    assert "not TikTok's own data" in line and "10 sources" in line and "possible tag(s)" in line


# --- how a run uses it ----------------------------------------------------------------------------


def test_a_run_uses_live_trends_when_enabled_and_hands_over_the_video(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return trends_mod.Trends(None, "Hot right now: X", 0.1, ("x",), False, False, source="feeds", verified=True, note="5 sources; a; US")

    monkeypatch.setattr(feeds, "auto_feeds", fake)
    found = cli.load_trends_for_run(None, env={"CLIPPER_LIVE_TRENDS": "1"}, niche="reality tv", region="UK", video_info=VIDEO)
    assert found.source == "feeds" and seen["video_info"] == VIDEO and seen["niche"] == "reality tv"
    assert seen["region"] == "UK" and seen["force"] is False


@pytest.mark.parametrize("env, auto", [({"CLIPPER_LIVE_TRENDS": "0"}, True), ({"CLIPPER_LIVE_TRENDS": "off"}, True), ({"CLIPPER_LIVE_TRENDS": "1"}, False)])
def test_live_trends_can_be_switched_off_for_good_or_for_one_run(monkeypatch, tmp_path, env, auto):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(feeds, "auto_feeds", lambda **kw: pytest.fail("must not be asked"))
    assert cli.load_trends_for_run(None, env=env, auto=auto) is None


def test_a_trends_file_you_wrote_beats_the_live_sources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trends.txt").write_text("#mine\n", encoding="utf-8")
    monkeypatch.setattr(feeds, "auto_feeds", lambda **kw: pytest.fail("must not be asked"))
    found = cli.load_trends_for_run(None, env={"CLIPPER_LIVE_TRENDS": "1"})
    assert found.source == "file" and "mine" in found.hashtags


def test_live_trends_are_on_unless_switched_off():
    assert cli.live_trends_enabled({}) and cli.live_trends_enabled({"CLIPPER_LIVE_TRENDS": "1"})
    assert not any(cli.live_trends_enabled({"CLIPPER_LIVE_TRENDS": v}) for v in ("0", "false", "No", "OFF"))


def test_the_trends_command_can_read_the_sources_now_and_say_how_each_went(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(feeds, "auto_feeds", lambda **kw: trends_mod.Trends(None, "Hot right now: X", 0.1, ("x",), False, False, source="feeds", verified=True, note="n"))
    assert cli.main(["trends", "--live"]) == 0
    out = capsys.readouterr().out
    assert "Using live trends from public feeds" in out and "Hot right now: X" in out
    monkeypatch.setattr(feeds, "auto_feeds", lambda **kw: None)
    assert cli.main(["trends", "--live"]) == 1
    assert "Nothing could be read" in capsys.readouterr().out


def test_init_sites_makes_the_file_once(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["trends", "--init-sites"]) == 0
    assert (tmp_path / "trend_sites.txt").is_file() and "trend_sites.txt" in capsys.readouterr().out


def test_the_saved_result_survives_a_round_trip_and_old_caches_still_load(tmp_path):
    result, _, _ = read(tmp_path)
    path = trend_research.cache_path("reality tv", "United States", f"{feeds.PLATFORM_KEY}|reality tv", tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["notes"] and saved["platform"] == feeds.PLATFORM_KEY
    del saved["notes"]  # a cache written before notes existed
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert trend_research.load_cache(path).notes == []
