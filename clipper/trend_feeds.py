"""Live trends: a "radar" of public sources, driven by your niche and by the video itself.

Not part of upstream. It refreshes on its own: a result is reused for a few hours, then read afresh
the next time a run needs it (and in the background while the app is open).

Why this does not read TikTok. TikTok publishes no data feed of its trends. Its Creative Center is a
dashboard that shows an anonymous visitor only a few hashtags and has no public API, and reading the
For You page automatically needs a logged-in session, which TikTok's terms forbid and which can get the
account limited. CLIPPER never touches TikTok or the account. If you want a TikTok-run site in the mix,
add its page to ``trend_sites.txt`` (see trend_sites.py): it is then read politely, like the others.

How it works:

1. Sources (trend_sources.py): Google Trends, Wikipedia, Bluesky, Mastodon, entertainment and gaming
   news, and, asked about your niche and the video's topic, Google and Bing News, search suggestions,
   YouTube's newest videos, and the video's own tags. Plus any websites you add yourself.
2. Ranking (trend_rank.py): something mentioned by several independent sources ranks above something
   one source says, and something hot that is also about your topic ranks highest.
3. The caption writer is handed the result and told to use a trend only when it fits the clip.

Each source is optional. A failed one is reported and skipped, and nothing here can stop a run. These
sources could not be reached from where this was written, so it is tested against realistic samples;
``clipper trends --live`` shows exactly how each one went on your own computer.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import trend_net, trend_rank, trend_research, trend_sites, trend_sources
from .trend_net import FeedError
from .trend_rank import Evidence, Items
from .trend_research import Report
from .trend_sources import Ctx, Skipped, Source
from .trends import STALE_AFTER_DAYS, Trends

LogFn = Callable[[str], None]
Fetch = Callable[[str], str]

REFRESH_HOURS = 6  # how long a result is reused. Trends fade in days, so this is plenty fresh.
RETRY_AFTER_FAILURE_HOURS = 1  # short: a laptop that was offline for a moment should try again soon
PARTIAL_KEEP_HOURS = 24  # a much thinner refresh does not replace a saved result younger than this
TOTAL_SECONDS = 30  # the whole refresh is capped, however many sources there are
WORKERS = 6
PLATFORM_KEY = "live-radar"  # keeps this cache apart from the AI web-research cache
MAX_SEEDS = 2
MAX_VIDEO_TAGS = 15
TEXT_LIMIT = 1800


# ---------------------------------------------------------------------------
# What the video itself tells us (no network)
# ---------------------------------------------------------------------------


def clean_title(title: Any) -> str:
    """The part of a video title that names the thing: "Love Is Blind S8 | Reunion | Netflix" -> "Love Is Blind S8"."""
    text = re.sub(r"#\w+", " ", str(title or ""))
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    first = re.split(r"\s+[|–—]\s+|\s+-\s+", text.strip())[0]
    words = re.sub(r"[^\w\s'&]", " ", first).split()
    words = [w for w in words if w.lower() not in ("official", "full", "episode", "hd", "4k", "trailer", "livestream")]
    return " ".join(words[:7])


def video_items(video_info: Mapping[str, Any] | None) -> Items:
    """The video's own tags and hashtags, as hashtag-shaped words, plus its title as supporting text."""
    info = video_info or {}
    tags: list[str] = []
    for raw in info.get("tags") or []:
        tag = trend_rank.tag_from(str(raw))
        if tag:
            tags.append(tag)
    for text in (info.get("title"), info.get("description")):
        tags += trend_sources.hashtags_in(str(text or ""))
    title = " ".join(str(info.get("title") or "").split())
    return Items(texts=[title] if title else [], tags=list(dict.fromkeys(tags))[:MAX_VIDEO_TAGS])


def video_seeds(video_info: Mapping[str, Any] | None, niche: str | None) -> tuple[str, ...]:
    """What to ask the topic-based sources about: the video's subject first, then the account's niche."""
    seeds: list[str] = []
    for text in (clean_title((video_info or {}).get("title")), " ".join((niche or "").split())):
        if text and text.lower() not in (s.lower() for s in seeds):
            seeds.append(text)
    return tuple(seeds[:MAX_SEEDS])


# ---------------------------------------------------------------------------
# Asking every source
# ---------------------------------------------------------------------------


def _site_sources(path: str | Path | None) -> tuple[list[Source], list[str]]:
    sites, problems = trend_sites.load_sites(path)
    sources: list[Source] = []
    for number, site in enumerate(sites, 1):

        def run(ctx: Ctx, fetch: Fetch, site: trend_sites.Site = site) -> Items:
            address = trend_sites.expand(site, ctx.seeds[0] if ctx.seeds else None)
            if address is None:
                raise Skipped("skipped: this address needs a topic ({query}) and there isn't one")
            allowed, why = trend_sites.robots_allows(address, fetch)
            if not allowed:
                raise Skipped(f"skipped: {why}")
            return trend_sites.extract_signals(fetch(address))

        sources.append(
            Source(f"site{number}", site.label, "niche" if site.seeded else "general", (site.host,), run)
        )
    return sources, problems


def _run_source(source: Source, ctx: Ctx, fetch: Fetch) -> tuple[str, str, Items]:
    try:
        items = source.run(ctx, fetch)
    except Skipped as exc:
        return "skipped", str(exc), Items()
    except FeedError as exc:
        return "failed", str(exc), Items()
    except Exception as exc:  # noqa: BLE001 - one broken source must never take the others down
        return "failed", f"unexpected problem ({str(exc)[:80]})", Items()
    return ("ok" if not items.is_empty() else "empty"), "", items


def _describe(source: Source, status: str, why: str, items: Items) -> str:
    if status == "ok":
        parts = [f"{n} {word}" for n, word in ((len(items.names), "names"), (len(items.texts), "headlines"), (len(items.tags), "tags")) if n]
        return f"{source.label}: " + ", ".join(parts)
    if status == "empty":
        return f"{source.label}: answered, but there was nothing on it"
    if status == "skipped":
        return f"{source.label}: {why}"
    return f"{source.label}: FAILED, {why}"


def gather(
    ctx: Ctx, sources: Sequence[Source], *, fetch: Fetch | None = None
) -> tuple[list[Evidence], list[str]]:
    """Ask every source at once (capped at TOTAL_SECONDS). Returns what came back and one plain line per source."""
    results: dict[str, tuple[str, str, Items]] = {}

    def fetch_for(source: Source) -> Fetch:
        return fetch if fetch is not None else (lambda url: trend_net.fetch_text(url, source.hosts))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
    futures = {pool.submit(_run_source, s, ctx, fetch_for(s)): s for s in sources}
    done, pending = concurrent.futures.wait(futures, timeout=TOTAL_SECONDS)
    for future in done:
        results[futures[future].id] = future.result()
    for future in pending:
        future.cancel()
        results[futures[future].id] = ("failed", "took too long", Items())
    pool.shutdown(wait=False, cancel_futures=True)

    evidence: list[Evidence] = []
    notes: list[str] = []
    for source in sources:
        status, why, items = results[source.id]
        notes.append(_describe(source, status, why, items))
        if status == "ok":
            evidence.append(Evidence(source.id, source.label, source.kind, items))
    return evidence, notes


# ---------------------------------------------------------------------------
# From what was read to what the caption writer sees
# ---------------------------------------------------------------------------


def build_report(
    ranked: trend_rank.Ranked,
    evidence: Sequence[Evidence],
    notes: Sequence[str],
    *,
    niche: str | None,
    region: str | None,
    now: float,
) -> Report:
    topics = [f"hot{c.tier}: {c.name} | {', '.join(c.sources)}" for c in ranked.hot]
    topics += [f"niche: {phrase} | {count}" for phrase, count in ranked.niche_phrases]
    topics += [f"search: {phrase}" for phrase in ranked.searching]
    video = next((e for e in evidence if e.id == "video"), None)
    topics += [f"video: {tag}" for tag in (video.items.tags if video else [])]
    return Report(
        hashtags=list(ranked.tags),
        topics=topics,
        verified=bool(evidence),  # real sources, not a model's recollection
        n_sources=len(evidence),
        fetched_at=now,
        niche=niche,
        region=region,
        platform=PLATFORM_KEY,
        notes=list(notes),
    )


def _entries(report: Report, prefix: str) -> list[str]:
    return [t[len(prefix):].strip() for t in report.topics if t.startswith(prefix)]


def _named(entries: list[str]) -> list[str]:
    out = []
    for entry in entries:
        name, _, where = entry.partition(" | ")
        out.append(f"{name} [{where}]" if where else name)
    return out


def to_trends(report: Report, *, now: float | None = None) -> Trends:
    """The trends in the form the caption prompt uses."""
    current = time.time() if now is None else now
    age_days = max(0.0, (current - report.fetched_at) / 86400)
    lines = [
        "Live public signals from several independent sources. These are NOT TikTok's own data. "
        "Something seen in more than one source is more likely to be really hot."
    ]
    if tied := _named(_entries(report, "hot0:")):
        lines.append("Hot right now AND tied to your topic or this video: " + "; ".join(tied))
    if several := _named(_entries(report, "hot1:")):
        lines.append("Seen in several sources: " + "; ".join(several))
    if single := _named(_entries(report, "hot2:")):
        lines.append("Hot right now (one source): " + "; ".join(single))
    if talk := [e.split(" | ")[0] for e in _entries(report, "niche:")]:
        lines.append(f"Being talked about in {report.niche or 'your niche'} this week: " + "; ".join(talk))
    if search := _entries(report, "search:"):
        lines.append("People are searching for: " + "; ".join(search))
    if video := _entries(report, "video:"):
        lines.append("Tags on the source video itself: " + " ".join(f"#{t}" for t in video))
    if report.hashtags:
        lines.append("Tags in use across the sources: " + " ".join(f"#{t}" for t in report.hashtags))
    text = "\n".join(lines)
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT].rsplit("\n", 1)[0]
    tags = tuple(dict.fromkeys([*report.hashtags, *_entries(report, "video:")]))
    return Trends(
        path=None,
        text=text,
        age_days=age_days,
        hashtags=tags,
        stale=age_days > STALE_AFTER_DAYS,
        truncated=False,
        source="feeds",
        verified=report.verified,
        note=f"{report.n_sources} sources; {report.niche or 'general'}; {report.region or 'US'}",
    )


# ---------------------------------------------------------------------------
# Refreshing on its own
# ---------------------------------------------------------------------------


def _cache_file(niche: str | None, region: str | None, seeds: Sequence[str], cache_dir: str | Path | None) -> Path:
    return trend_research.cache_path(niche, region, f"{PLATFORM_KEY}|{'+'.join(seeds)}", cache_dir)


def _fail_path(cache_file: Path) -> Path:
    return cache_file.with_suffix(".fail")


def _recent_failure(cache_file: Path, now: float) -> dict[str, Any] | None:
    try:
        marker = json.loads(_fail_path(cache_file).read_text(encoding="utf-8"))
        if now - float(marker["at"]) < RETRY_AFTER_FAILURE_HOURS * 3600:
            return marker
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _record_failure(cache_file: Path, now: float, reason: str) -> None:
    try:
        _fail_path(cache_file).parent.mkdir(parents=True, exist_ok=True)
        _fail_path(cache_file).write_text(json.dumps({"at": now, "reason": reason[:160]}), encoding="utf-8")
    except OSError:
        pass  # without the marker it simply tries again sooner


def _clear_failure(cache_file: Path) -> None:
    try:
        _fail_path(cache_file).unlink()
    except OSError:
        pass


def auto_feeds(
    *,
    niche: str | None = None,
    region: str | None = None,
    video_info: Mapping[str, Any] | None = None,
    force: bool = False,
    now: float | None = None,
    cache_dir: str | Path | None = None,
    log: LogFn | None = None,
    fetch: Fetch | None = None,
    env: Mapping[str, str] | None = None,
    sites_path: str | Path | None = None,
    ytdlp_search: trend_sources.YtSearch | None = None,
) -> Trends | None:
    """Live trends for this niche, country and video: the saved copy while it is under REFRESH_HOURS old,
    otherwise read afresh. Never raises. If nothing at all can be read it says why, keeps using the last
    saved copy (flagged stale if old), and tries again after RETRY_AFTER_FAILURE_HOURS."""
    log = log or (lambda m: None)
    env = os.environ if env is None else env
    current = time.time() if now is None else now
    seeds = video_seeds(video_info, niche)
    path = _cache_file(niche, region, seeds, cache_dir)
    cached = trend_research.load_cache(path)

    if cached and not force and (current - cached.fetched_at) < REFRESH_HOURS * 3600:
        return to_trends(cached, now=current)
    if not force and (recent := _recent_failure(path, current)):
        log(f"Live trends couldn't be read recently ({recent.get('reason', 'unknown')}); trying again within {RETRY_AFTER_FAILURE_HOURS}h.")
        return to_trends(cached, now=current) if cached else None

    log("Reading what's trending right now (public sources)...")
    notes: list[str] = []
    try:
        site_sources, site_problems = _site_sources(sites_path)
        sources = [*trend_sources.select(env.get("CLIPPER_TREND_SOURCES")), *site_sources]
        ctx = Ctx(
            niche=niche,
            geo=trend_sources.geo_code(region),
            now=current,
            seeds=seeds,
            video=video_items(video_info),
            ytdlp_search=ytdlp_search or trend_sources.default_ytdlp_search(env.get("CLIPPER_COOKIES") or "cookies.txt"),
            category=trend_sources.category_for(niche, *seeds),
        )
        evidence, notes = gather(ctx, sources, fetch=fetch)
        notes += [f"trend_sites.txt: {p}" for p in site_problems]
        for line in notes:
            log(f"   {line}")
        ranked = trend_rank.rank(evidence)
        report = build_report(ranked, evidence, notes, niche=niche, region=region or ctx.geo, now=current)
    except Exception as exc:  # noqa: BLE001 - this must never stop a run
        log(f"   unexpected problem reading trends: {str(exc)[:120]}")
        report = None

    # A refresh that got far less than last time (a flaky connection) must not replace a good recent result.
    if (
        report is not None and cached is not None and not report.is_empty()
        and report.n_sources * 2 < cached.n_sources
        and (current - cached.fetched_at) < PARTIAL_KEEP_HOURS * 3600
    ):
        log(
            f"Only {report.n_sources} source(s) answered this time against {cached.n_sources} last time, "
            "so the saved result is being kept."
        )
        _record_failure(path, current, "only a few sources answered")
        return to_trends(cached, now=current)

    if report is not None and not report.is_empty():
        try:
            trend_research.save_cache(path, report)
        except OSError:
            pass  # still usable this run
        _clear_failure(path)
        return to_trends(report, now=current)

    failed = [n for n in notes if "FAILED" in n]
    _record_failure(path, current, "; ".join(failed)[:150] or "nothing came back")
    if cached:
        log("Using the last saved live trends instead.")
        return to_trends(cached, now=current)
    return None


def status(
    niche: str | None = None,
    region: str | None = None,
    *,
    now: float | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """What the app shows: when the account-level trends were last read and how it went. Local files only."""
    current = time.time() if now is None else now
    seeds = video_seeds(None, niche)
    path = _cache_file(niche, region, seeds, cache_dir)
    cached = trend_research.load_cache(path)
    failure = _recent_failure(path, current)
    return {
        "updated": cached.fetched_at if cached else None,
        "age_hours": round((current - cached.fetched_at) / 3600, 1) if cached else None,
        "sources": cached.n_sources if cached else 0,
        "tags": list(cached.hashtags) if cached else [],
        "hot": [e.split(" | ")[0] for e in _entries(cached, "hot0:") + _entries(cached, "hot1:")][:8] if cached else [],
        "notes": list(cached.notes) if cached else [],
        "failure": failure.get("reason") if failure else None,
        "due": cached is None or (current - cached.fetched_at) >= REFRESH_HOURS * 3600,
    }
