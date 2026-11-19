"""The built-in public sources of "what is hot right now": how to ask each one, and how to read it.

Not part of upstream. Every source is optional and independent. None needs a login or a key, and
none is TikTok itself (see trend_feeds.py for why). They fall in two kinds:

* ``general`` sources say what is hot for everyone: Google's trending searches, Wikipedia's most-read
  pages, Bluesky's and Mastodon's trending topics, entertainment and gaming news feeds.
* ``niche`` sources are asked about the person's topic or the video in hand: news searches, search
  suggestions, YouTube's newest videos on the topic, and the video's own tags.

Replies are read with plain patterns or JSON, not an XML parser: this is data from the internet.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .trend_net import FeedError
from .trend_rank import Items

Fetch = Callable[[str], str]
YtSearch = Callable[[str, int], list[dict[str, Any]]]

MAX_TITLES = 30
MAX_SUGGESTIONS = 10
REGIONS = {
    "united states": "US", "usa": "US", "us": "US", "america": "US", "united states of america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
    "canada": "CA", "australia": "AU", "ireland": "IE", "new zealand": "NZ", "india": "IN",
    "germany": "DE", "france": "FR", "brazil": "BR", "mexico": "MX", "philippines": "PH",
    "south africa": "ZA", "nigeria": "NG", "singapore": "SG", "spain": "ES", "italy": "IT",
    "japan": "JP", "netherlands": "NL", "sweden": "SE", "poland": "PL", "turkey": "TR",
    "indonesia": "ID", "malaysia": "MY", "pakistan": "PK", "kenya": "KE", "argentina": "AR",
}
SKIP_WIKI = ("special:", "wikipedia:", "portal:", "help:", "file:", "category:", "talk:", "user:", "template:", "draft:")
SKIP_WIKI_STARTS = ("deaths in", "list of", "lists of", "main page")
GAMING_WORDS = frozenset("game games gaming gamer gta minecraft fortnite roblox twitch streamer esports xbox playstation nintendo valorant".split())
RSS_FEEDS = {
    "entertainment": (
        "https://variety.com/feed/",
        "https://deadline.com/feed/",
        "https://www.tmz.com/rss.xml",
        "https://knowyourmeme.com/newsfeed.rss",
    ),
    "gaming": (
        "https://feeds.ign.com/ign/all",
        "https://kotaku.com/rss",
        "https://www.polygon.com/rss/index.xml",
        "https://knowyourmeme.com/newsfeed.rss",
    ),
}


class Skipped(FeedError):
    """A source that did not run because it had nothing to work with (not a failure)."""


@dataclass(frozen=True)
class Ctx:
    niche: str | None
    geo: str
    now: float
    seeds: tuple[str, ...] = ()  # what to look for: the person's niche and the video's topic
    video: Items | None = None  # the source video's own tags, read without any network
    ytdlp_search: YtSearch | None = None
    category: str = "entertainment"


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    kind: str  # "general" | "niche"
    hosts: tuple[str, ...]
    run: Callable[[Ctx, Fetch], Items]
    default: bool = True


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def geo_code(region: str | None) -> str:
    """A two-letter country code from free text like "United States" or "uk"; US when unknown."""
    text = " ".join((region or "").lower().split())
    if re.fullmatch(r"[a-z]{2}", text):
        return text.upper()
    return REGIONS.get(text, "US")


def category_for(*texts: str | None) -> str:
    words = set(re.findall(r"[a-z0-9]+", " ".join(t or "" for t in texts).lower()))
    return "gaming" if words & GAMING_WORDS else "entertainment"


def _text(raw: str) -> str:
    raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw, flags=re.S)
    raw = re.sub(r"<[^>]+>", " ", html.unescape(raw))
    return " ".join(html.unescape(raw).split())


def strip_publisher(title: str) -> str:
    """"Headline here - The Publisher" -> "Headline here" (Google News adds the publisher)."""
    return re.sub(r"\s+[-|]\s+[^-|]{2,40}$", "", title).strip()


def parse_rss_titles(text: str, limit: int = MAX_TITLES) -> list[str]:
    """Item titles from an RSS or Atom feed. The feed's own title is not an item, so it is skipped."""
    titles = []
    for block in re.findall(r"<(?:item|entry)[\s>].*?</(?:item|entry)>", text, flags=re.S | re.I):
        match = re.search(r"<title[^>]*>(.*?)</title>", block, flags=re.S | re.I)
        title = strip_publisher(_text(match.group(1))) if match else ""
        if title:
            titles.append(title[:140])
    return titles[:limit]


def parse_google_trends(text: str) -> list[dict[str, Any]]:
    """Topics from Google Trends' RSS feed: ``[{"topic", "traffic", "news"}]``, most searched first.

    The namespace prefix is ignored on purpose; Google has changed it before.
    """
    items = []
    for block in re.findall(r"<item>(.*?)</item>", text, flags=re.S | re.I):
        title = re.search(r"<title[^>]*>(.*?)</title>", block, flags=re.S | re.I)
        topic = _text(title.group(1)) if title else ""
        if not topic:
            continue
        traffic = re.search(r"<[\w-]+:approx_traffic[^>]*>(.*?)</[\w-]+:approx_traffic>", block, flags=re.S | re.I)
        news = [
            _text(m)
            for m in re.findall(r"<[\w-]+:news_item_title[^>]*>(.*?)</[\w-]+:news_item_title>", block, flags=re.S | re.I)
        ]
        items.append({"topic": topic[:80], "traffic": _text(traffic.group(1)) if traffic else "", "news": [n for n in news if n][:2]})
    return items[:15]


def parse_wikipedia_top(text: str) -> list[str]:
    """Article names from Wikimedia's "most viewed on a day" reply, without the site's own pages."""
    try:
        articles = json.loads(text)["items"][0]["articles"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise FeedError("the reply wasn't the list it should be") from None
    names = []
    for article in articles if isinstance(articles, list) else []:
        raw = str(article.get("article", "")) if isinstance(article, Mapping) else ""
        name = urllib.parse.unquote(raw).replace("_", " ").strip()
        low = name.lower()
        if not name or name == "-" or low.startswith(SKIP_WIKI) or low.startswith(SKIP_WIKI_STARTS):
            continue
        names.append(name[:80])
    return names[:15]


def parse_suggestions(text: str) -> list[str]:
    """Search suggestions from the ``["query", ["suggestion", ...]]`` reply that Google and Bing both use."""
    try:
        suggestions = json.loads(text)[1]
    except (ValueError, IndexError, TypeError, KeyError):
        raise FeedError("the reply wasn't a list of suggestions") from None
    return [s.strip()[:80] for s in suggestions if isinstance(s, str) and s.strip()][:MAX_SUGGESTIONS]


def parse_bluesky(text: str) -> list[str]:
    """Trending topics from Bluesky's public ``getTrendingTopics`` reply."""
    try:
        topics = json.loads(text)["topics"]
    except (ValueError, KeyError, TypeError):
        raise FeedError("the reply wasn't a list of topics") from None
    names = []
    for topic in topics if isinstance(topics, list) else []:
        name = topic.get("topic") if isinstance(topic, Mapping) else topic
        if isinstance(name, str) and name.strip():
            names.append(name.strip()[:60])
    return names[:25]


def parse_mastodon(text: str) -> list[str]:
    """Trending hashtag names from a Mastodon server's public ``/api/v1/trends/tags`` reply."""
    try:
        data = json.loads(text)
    except ValueError:
        raise FeedError("the reply wasn't a list of tags") from None
    return [str(t["name"]) for t in data if isinstance(t, Mapping) and t.get("name")][:20] if isinstance(data, list) else []


def parse_reddit(text: str) -> list[str]:
    """Post titles from a Reddit listing (``{"data": {"children": [...]}}``)."""
    try:
        children = json.loads(text)["data"]["children"]
    except (ValueError, KeyError, TypeError):
        raise FeedError("the reply wasn't a list of posts") from None
    titles = []
    for child in children if isinstance(children, list) else []:
        data = child.get("data") if isinstance(child, Mapping) else None
        title = data.get("title") if isinstance(data, Mapping) else None
        if isinstance(title, str) and title.strip():
            titles.append(" ".join(title.split())[:140])
    return titles[:25]


def hashtags_in(text: str) -> list[str]:
    return [m.lower() for m in re.findall(r"(?<![\w&/])#([A-Za-z][A-Za-z0-9_]{2,29})\b", text or "")]


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def google_trends_url(geo: str) -> str:
    return f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(geo)}"


def wikipedia_url(day: datetime) -> str:
    return f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{day:%Y/%m/%d}"


def bluesky_url() -> str:
    return "https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics?limit=25"


def mastodon_url() -> str:
    return "https://mastodon.social/api/v1/trends/tags?limit=20"


def google_news_url(seed: str, geo: str) -> str:
    return (
        f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(seed + ' when:7d')}"
        f"&hl=en-{geo}&gl={geo}&ceid={geo}:en"
    )


def bing_news_url(seed: str) -> str:
    return f"https://www.bing.com/news/search?q={urllib.parse.quote_plus(seed)}&format=rss"


def google_suggest_url(seed: str) -> str:
    return f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(seed)}"


def bing_suggest_url(seed: str) -> str:
    return f"https://api.bing.com/osjson.aspx?query={urllib.parse.quote(seed)}"


def reddit_url(seed: str) -> str:
    return f"https://www.reddit.com/search.json?q={urllib.parse.quote_plus(seed)}&sort=top&t=week&limit=25"


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------


def _need_seeds(ctx: Ctx) -> tuple[str, ...]:
    if not ctx.seeds:
        raise Skipped("skipped: it needs your niche (CLIPPER_NICHE) or a video to ask about")
    return ctx.seeds


def _merge_titles(fetch: Fetch, urls: list[str], reader: Callable[[str], list[str]]) -> list[str]:
    """Read several addresses; keep what worked. Fails only if every one failed."""
    found: list[str] = []
    last: FeedError | None = None
    ok = False
    for url in urls:
        try:
            found += reader(fetch(url))
            ok = True
        except FeedError as exc:
            last = exc
    if not ok and last is not None:
        raise last
    return list(dict.fromkeys(found))


def _google_trends(ctx: Ctx, fetch: Fetch) -> Items:
    items = parse_google_trends(fetch(google_trends_url(ctx.geo)))
    return Items(names=[i["topic"] for i in items], texts=[n for i in items for n in i["news"]])


def _wikipedia(ctx: Ctx, fetch: Fetch) -> Items:
    today = datetime.fromtimestamp(ctx.now, tz=timezone.utc)
    last: FeedError | None = None
    for back in (1, 2):  # yesterday's page may not be published yet in the early hours
        try:
            return Items(names=parse_wikipedia_top(fetch(wikipedia_url(today - timedelta(days=back)))))
        except FeedError as exc:
            last = exc
    raise last or FeedError("no day available")


def _bluesky(ctx: Ctx, fetch: Fetch) -> Items:
    return Items(names=parse_bluesky(fetch(bluesky_url())))


def _mastodon(ctx: Ctx, fetch: Fetch) -> Items:
    return Items(tags=parse_mastodon(fetch(mastodon_url())))


def _entertainment_rss(ctx: Ctx, fetch: Fetch) -> Items:
    titles = _merge_titles(fetch, list(RSS_FEEDS.get(ctx.category, RSS_FEEDS["entertainment"])), parse_rss_titles)
    return Items(texts=titles[:80])


def _google_news(ctx: Ctx, fetch: Fetch) -> Items:
    urls = [google_news_url(s, ctx.geo) for s in _need_seeds(ctx)]
    return Items(texts=_merge_titles(fetch, urls, parse_rss_titles))


def _bing_news(ctx: Ctx, fetch: Fetch) -> Items:
    urls = [bing_news_url(s) for s in _need_seeds(ctx)]
    return Items(texts=_merge_titles(fetch, urls, parse_rss_titles))


def _suggest_google(ctx: Ctx, fetch: Fetch) -> Items:
    urls = [google_suggest_url(s) for s in _need_seeds(ctx)]
    return Items(texts=_merge_titles(fetch, urls, parse_suggestions))


def _suggest_bing(ctx: Ctx, fetch: Fetch) -> Items:
    urls = [bing_suggest_url(s) for s in _need_seeds(ctx)]
    return Items(texts=_merge_titles(fetch, urls, parse_suggestions))


def _reddit(ctx: Ctx, fetch: Fetch) -> Items:
    urls = [reddit_url(s) for s in _need_seeds(ctx)]
    return Items(texts=_merge_titles(fetch, urls, parse_reddit))


def _youtube_recent(ctx: Ctx, fetch: Fetch) -> Items:
    """The newest YouTube videos about the topic: their titles, and the hashtags creators put in them."""
    seeds = _need_seeds(ctx)
    if ctx.ytdlp_search is None:
        raise Skipped("skipped: YouTube search isn't available here")
    entries: list[dict[str, Any]] = []
    for seed in seeds:
        entries += ctx.ytdlp_search(seed, 15)
    entries.sort(key=lambda e: -(e.get("view_count") or 0))
    titles = [" ".join(str(e.get("title") or "").split()) for e in entries[:12]]
    titles = [t for t in dict.fromkeys(titles) if t]
    return Items(texts=titles, tags=[tag for t in titles for tag in hashtags_in(t)])


def _this_video(ctx: Ctx, fetch: Fetch) -> Items:
    if ctx.video is None or ctx.video.is_empty():
        raise Skipped("skipped: no tags on this video")
    return ctx.video


GOOGLE_HOSTS = ("trends.google.com", "news.google.com", "suggestqueries.google.com")
ALL_RSS_HOSTS = tuple(
    sorted({urllib.parse.urlsplit(u).hostname for feeds in RSS_FEEDS.values() for u in feeds if urllib.parse.urlsplit(u).hostname})
)

BUILTIN: tuple[Source, ...] = (
    Source("google_trends", "Google Trends", "general", ("trends.google.com",), _google_trends),
    Source("wikipedia", "Wikipedia most-read", "general", ("wikimedia.org",), _wikipedia),
    Source("bluesky", "Bluesky trending", "general", ("public.api.bsky.app",), _bluesky),
    Source("mastodon", "Mastodon trending tags", "general", ("mastodon.social",), _mastodon),
    Source("news_feeds", "Entertainment and gaming news", "general", ALL_RSS_HOSTS, _entertainment_rss),
    Source("google_news", "Google News", "niche", ("news.google.com",), _google_news),
    Source("bing_news", "Bing News", "niche", ("www.bing.com",), _bing_news),
    Source("suggest_google", "Google/YouTube search suggestions", "niche", ("suggestqueries.google.com",), _suggest_google),
    Source("suggest_bing", "Bing search suggestions", "niche", ("api.bing.com",), _suggest_bing),
    Source("youtube", "Newest YouTube videos on the topic", "niche", (), _youtube_recent),
    Source("video", "This video's own tags", "niche", (), _this_video),
    # Reddit restricts automated access, so it is opt-in: CLIPPER_TREND_SOURCES=default,reddit
    Source("reddit", "Reddit top posts this week", "niche", ("www.reddit.com",), _reddit, default=False),
)
BY_ID = {s.id: s for s in BUILTIN}


def select(setting: str | None) -> list[Source]:
    """The sources to use. ``CLIPPER_TREND_SOURCES`` is "default" (or empty), "all", or a comma list of ids."""
    text = (setting or "").strip().lower()
    if text in ("", "default"):
        return [s for s in BUILTIN if s.default]
    if text == "all":
        return list(BUILTIN)
    wanted = [part.strip() for part in re.split(r"[,\s]+", text) if part.strip()]
    chosen = [s for s in BUILTIN if s.default] if "default" in wanted else []
    chosen += [BY_ID[w] for w in wanted if w in BY_ID and BY_ID[w] not in chosen]
    return chosen


def all_hosts() -> frozenset[str]:
    return frozenset(h for s in BUILTIN for h in s.hosts)


def default_ytdlp_search(cookies: str | Path | None = None) -> YtSearch:
    """A YouTube search (newest first, titles and view counts only) using yt-dlp, which is already installed."""

    def search(seed: str, count: int) -> list[dict[str, Any]]:
        try:
            import yt_dlp
        except ImportError:
            raise FeedError("yt-dlp isn't installed") from None
        options: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
            "socket_timeout": 10, "playlistend": count,
        }
        if cookies and Path(cookies).is_file():
            options["cookiefile"] = str(cookies)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearchdate{count}:{seed}", download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many kinds; all mean "couldn't search"
            raise FeedError(f"YouTube search failed ({str(exc)[:80]})") from exc
        return [e for e in ((info or {}).get("entries") or []) if isinstance(e, dict)]

    return search
