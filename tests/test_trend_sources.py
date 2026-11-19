import json
from datetime import datetime, timezone

import pytest

from clipper import trend_sources as src
from clipper.trend_net import FeedError
from clipper.trend_rank import Items

GOOGLE_RSS = """<?xml version="1.0"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>
<item><title>Love Is Blind</title><ht:approx_traffic>200K+</ht:approx_traffic>
 <ht:news_item><ht:news_item_title>Love Is Blind reunion: who said what &amp; why</ht:news_item_title></ht:news_item>
 <ht:news_item><ht:news_item_title><![CDATA[Second headline]]></ht:news_item_title></ht:news_item></item>
<item><title><![CDATA[Chiefs vs Bills]]></title><ht:approx_traffic>500K+</ht:approx_traffic></item>
<item><title></title></item>
</channel></rss>"""
WIKI = json.dumps({"items": [{"articles": [
    {"article": "Main_Page", "views": 9, "rank": 1},
    {"article": "Love_Is_Blind_(TV_series)", "views": 8, "rank": 2},
    {"article": "Special:Search", "views": 7, "rank": 3},
    {"article": "Deaths_in_2026", "views": 6, "rank": 4},
    {"article": "Caf%C3%A9_Society", "views": 5, "rank": 5},
    {"article": "-", "views": 4, "rank": 6},
]}]})
SUGGEST = json.dumps(["love is blind", ["love is blind reunion", "love is blind season 9", "", 7]])
ATOM_AND_RSS = """<rss><channel><title>The Feed</title>
<item><title>Big news today - The Publisher</title></item>
<item><title><![CDATA[Another one | Some Site]]></title></item></channel></rss>
<feed><title>Atom feed</title><entry><title>Atom entry</title></entry></feed>"""


def test_google_trends_reads_topics_traffic_and_headlines_whatever_the_namespace():
    items = src.parse_google_trends(GOOGLE_RSS)
    assert [i["topic"] for i in items] == ["Love Is Blind", "Chiefs vs Bills"]  # the channel title and the empty item are skipped
    assert items[0]["traffic"] == "200K+"
    assert items[0]["news"] == ["Love Is Blind reunion: who said what & why", "Second headline"]
    old_namespace = GOOGLE_RSS.replace("ht:", "old:")
    assert [i["topic"] for i in src.parse_google_trends(old_namespace)] == ["Love Is Blind", "Chiefs vs Bills"]


def test_wikipedia_skips_the_sites_own_pages_and_decodes_names():
    assert src.parse_wikipedia_top(WIKI) == ["Love Is Blind (TV series)", "Café Society"]
    for bad in ("not json", json.dumps({"items": []}), json.dumps({})):
        with pytest.raises(FeedError):
            src.parse_wikipedia_top(bad)


def test_suggestions_keep_only_real_strings():
    assert src.parse_suggestions(SUGGEST) == ["love is blind reunion", "love is blind season 9"]
    with pytest.raises(FeedError):
        src.parse_suggestions("nope")


def test_rss_and_atom_titles_without_the_feed_title_or_the_publisher():
    assert src.parse_rss_titles(ATOM_AND_RSS) == ["Big news today", "Another one", "Atom entry"]


def test_bluesky_mastodon_and_reddit():
    bluesky = json.dumps({"topics": [{"topic": "Love Is Blind", "link": "/x"}, "Plain String", {"nope": 1}]})
    assert src.parse_bluesky(bluesky) == ["Love Is Blind", "Plain String"]
    assert src.parse_mastodon(json.dumps([{"name": "caturday"}, {"x": 1}, {"name": "art"}])) == ["caturday", "art"]
    reddit = json.dumps({"data": {"children": [{"data": {"title": "  A   post  "}}, {"data": {}}, "junk"]}})
    assert src.parse_reddit(reddit) == ["A post"]
    for parse in (src.parse_bluesky, src.parse_reddit):
        with pytest.raises(FeedError):
            parse("[]")


def test_hashtags_in_text_ignores_entities_and_short_things():
    assert src.hashtags_in("Wow #LoveIsBlind &#39; #ab and #Reunion2026 x#nope") == ["loveisblind", "reunion2026"]


def test_region_names_become_country_codes_and_unknown_ones_fall_back_to_us():
    assert src.geo_code("United Kingdom") == "GB" and src.geo_code("  canada ") == "CA" and src.geo_code("de") == "DE"
    assert src.geo_code("") == "US" and src.geo_code("worldwide") == "US" and src.geo_code(None) == "US"


def test_gaming_topics_get_gaming_feeds():
    assert src.category_for("GTA 6 streamers") == "gaming"
    assert src.category_for("reality TV drama") == "entertainment"


def test_select_defaults_all_ids_and_unknown_ids():
    default_ids = {s.id for s in src.select(None)}
    assert "google_trends" in default_ids and "reddit" not in default_ids  # Reddit is opt-in
    assert {s.id for s in src.select("all")} == {s.id for s in src.BUILTIN}
    assert [s.id for s in src.select("google_trends, wikipedia, nonsense")] == ["google_trends", "wikipedia"]
    assert {s.id for s in src.select("default,reddit")} == default_ids | {"reddit"}


def run(source_id, ctx, answers):
    """Run one source with canned replies. A reply that is an exception is raised."""
    seen = []

    def fetch(url):
        seen.append(url)
        for needle, answer in answers.items():
            if needle in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise FeedError("HTTP 404", status=404)

    return src.BY_ID[source_id].run(ctx, fetch), seen


def ctx(**kw):
    base = dict(niche="reality tv", geo="US", now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc).timestamp(), seeds=("love is blind", "reality tv"))
    base.update(kw)
    return src.Ctx(**base)


def test_niche_sources_say_they_were_skipped_when_there_is_nothing_to_ask_about():
    for source_id in ("google_news", "bing_news", "suggest_google", "suggest_bing", "reddit", "youtube"):
        with pytest.raises(src.Skipped):
            run(source_id, ctx(seeds=()), {})


def test_wikipedia_falls_back_to_the_day_before_when_yesterday_is_not_out_yet():
    items, seen = run("wikipedia", ctx(), {"2026/11/18": FeedError("HTTP 404", status=404), "2026/11/17": WIKI})
    assert items.names == ["Love Is Blind (TV series)", "Café Society"]
    assert seen[0].endswith("2026/11/18") and seen[1].endswith("2026/11/17")
    with pytest.raises(FeedError):
        run("wikipedia", ctx(), {})


def test_a_topic_source_asks_about_every_seed_and_survives_one_failing():
    items, seen = run("suggest_google", ctx(), {"q=love%20is%20blind": SUGGEST, "q=reality%20tv": FeedError("slow")})
    assert len(seen) == 2 and items.texts == ["love is blind reunion", "love is blind season 9"]
    with pytest.raises(FeedError):
        run("suggest_google", ctx(), {"suggestqueries": FeedError("down")})


def test_news_feeds_share_titles_across_feeds_and_tolerate_one_dead_feed():
    answers = {"variety.com": ATOM_AND_RSS, "deadline.com": FeedError("HTTP 500", status=500), "tmz.com": ATOM_AND_RSS}
    items, _ = run("news_feeds", ctx(), answers)
    assert items.texts == ["Big news today", "Another one", "Atom entry"]  # duplicates across feeds collapse


def test_youtube_uses_the_most_viewed_newest_videos_and_their_hashtags():
    entries = [
        {"title": "Reunion goes off #LoveIsBlind", "view_count": 900},
        {"title": "Nobody watches this", "view_count": 5},
        {"title": "Reunion goes off #LoveIsBlind", "view_count": 800},  # same title: counted once
    ]
    context = ctx(seeds=("love is blind",), ytdlp_search=lambda seed, n: entries)
    items, _ = run("youtube", context, {})
    assert items.texts == ["Reunion goes off #LoveIsBlind", "Nobody watches this"]
    assert items.tags == ["loveisblind"]


def test_the_videos_own_tags_are_a_source_with_no_network():
    items, seen = run("video", ctx(video=Items(tags=["loveisblind"])), {})
    assert items.tags == ["loveisblind"] and seen == []
    with pytest.raises(src.Skipped):
        run("video", ctx(video=Items()), {})


def test_every_address_a_source_can_build_is_on_that_sources_list_of_hosts():
    import urllib.parse

    day = datetime(2026, 11, 18)
    built = {
        "google_trends": [src.google_trends_url("US")],
        "wikipedia": [src.wikipedia_url(day)],
        "bluesky": [src.bluesky_url()],
        "mastodon": [src.mastodon_url()],
        "google_news": [src.google_news_url("a b", "GB")],
        "bing_news": [src.bing_news_url("a b")],
        "suggest_google": [src.google_suggest_url("a b")],
        "suggest_bing": [src.bing_suggest_url("a b")],
        "reddit": [src.reddit_url("a b")],
        "news_feeds": [u for feeds in src.RSS_FEEDS.values() for u in feeds],
    }
    for source_id, urls in built.items():
        hosts = set(src.BY_ID[source_id].hosts)
        for url in urls:
            parts = urllib.parse.urlsplit(url)
            assert parts.scheme == "https" and parts.hostname in hosts, (source_id, url)
