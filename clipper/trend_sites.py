"""Websites you add yourself, such as trend or virality sites, read politely.

Not part of upstream. Many sites show what is hot (hashtag rankings, trend dashboards, "what's going
viral" pages) and none has a standard way for a program to ask. So you name the pages, in a plain file,
and CLIPPER reads each one with a generic reader that picks out the hashtags and headlines on it.
What keeps this decent:

* It identifies itself, never sends cookies or a login, and does not run the page's scripts. A page
  that needs a login or only fills itself in with JavaScript simply gives nothing, and says so.
* It asks the site's robots.txt first and skips a page the site asks robots not to read.
* Each page is read at most once every few hours (the result is cached with the other sources).
* A site can only add hashtags and supporting text. It cannot put an invented "trend" at the top by
  itself: a name only counts as hot when other, independent sources agree (see trend_rank.py).

The file is ``trend_sites.txt``: one address per line, an optional label after a ``|``, ``//`` for a
comment. ``{query}`` in an address is replaced with the topic of your niche or video, so a site with a
search or "related hashtags" page can be asked about *this* video.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .trend_net import ROBOTS_NAME, FeedError
from .trend_rank import Items

SITES_FILE = "trend_sites.txt"
MAX_TEXTS = 80
MAX_TAGS = 40

TEMPLATE = """\
// Websites CLIPPER reads for what is trending, in addition to its built-in sources.
// One address per line. After a | you can add a label. Lines starting with // are ignored.
// {query} in an address is replaced with your niche or the video's topic (words joined by +),
// {tag} with the same thing as one lowercase word, for sites that have a page per hashtag.
//
// Only pages a person can read without logging in will give anything. Pages that need a login, or that
// fill themselves in with JavaScript, return nothing and `clipper trends --live` will say so.
// CLIPPER reads each page at most every few hours and skips pages that robots.txt asks bots to skip.
//
// Examples (remove the // to turn one on, and swap in the sites you actually use):
// https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en | TikTok Creative Center
// https://best-hashtags.com/hashtag/{tag}/ | Best-Hashtags related tags
"""


@dataclass(frozen=True)
class Site:
    url: str
    label: str
    host: str

    @property
    def seeded(self) -> bool:
        return "{query}" in self.url or "{tag}" in self.url


def _public_host(host: str) -> bool:
    if not host or "." not in host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True  # a name, not a numeric address
    return False


def parse_sites(text: str) -> tuple[list[Site], list[str]]:
    """The sites in the file, and a plain-English line for each one that could not be used."""
    sites: list[Site] = []
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        address, _, label = line.partition("|")
        address = address.strip()
        parts = urllib.parse.urlsplit(address.replace("{query}", "x").replace("{tag}", "x"))
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or not _public_host(host):
            problems.append(f"line {number}: only normal https:// addresses of public websites can be used")
            continue
        sites.append(Site(url=address, label=label.strip() or host, host=host))
    return sites, problems


def default_path() -> Path:
    return Path(SITES_FILE)


def load_sites(path: str | Path | None = None) -> tuple[list[Site], list[str]]:
    file = Path(path) if path else default_path()
    try:
        return parse_sites(file.read_text(encoding="utf-8"))
    except OSError:
        return [], []


def init_template(path: str | Path | None = None) -> Path:
    file = Path(path) if path else default_path()
    if not file.exists():
        file.write_text(TEMPLATE, encoding="utf-8")
    return file


def expand(site: Site, seed: str | None) -> str | None:
    """The address to read, with ``{query}``/``{tag}`` filled in. None if it needs a topic and there is none."""
    if not site.seeded:
        return site.url
    words = " ".join((seed or "").split())
    if not words:
        return None
    tag = re.sub(r"[^a-z0-9]", "", words.lower())
    if "{tag}" in site.url and not tag:
        return None
    return site.url.replace("{query}", urllib.parse.quote_plus(words)).replace("{tag}", tag)


_SKIP = frozenset({"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "footer", "form"})
_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "li", "a", "p", "td", "span", "title"})
_HASHTAG = re.compile(r"(?<![\w&/])#([A-Za-z][A-Za-z0-9_]{2,29})\b")


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.texts: list[str] = []
        self.tags: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in _SKIP:
            self.skip += 1
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self.skip:
            self.skip -= 1
        while self._stack:
            if self._stack.pop() == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.tags += [m.group(1).lower() for m in _HASHTAG.finditer(text)]
        if any(t in _TEXT_TAGS for t in self._stack) and 4 <= len(text) <= 90:
            self.texts.append(text)


def extract_signals(html_text: str) -> Items:
    """The hashtags and short headlines on a page. Scripts, styles and menus are ignored."""
    collector = _Collector()
    try:
        collector.feed(html_text)
        collector.close()
    except Exception:  # noqa: BLE001 - a broken page just gives what was read so far
        pass  # nosec B110
    tags = list(dict.fromkeys(collector.tags))[:MAX_TAGS]
    texts = list(dict.fromkeys(collector.texts))[:MAX_TEXTS]
    return Items(texts=texts, tags=tags)


def robots_allows(url: str, fetch: Callable[[str], str]) -> tuple[bool, str]:
    """Does the site's robots.txt let CLIPPER read this page? (allowed, a note if it is not)"""
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.hostname}/robots.txt"
    try:
        body = fetch(robots_url)
    except FeedError as exc:
        if exc.status is not None and 400 <= exc.status < 500:
            return True, ""  # no robots.txt: nothing is asked of us
        return False, "couldn't read the site's robots.txt, so it was skipped to be safe"
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    if parser.can_fetch(ROBOTS_NAME, url):
        return True, ""
    return False, "the site's robots.txt asks robots not to read this page, so it was skipped"
