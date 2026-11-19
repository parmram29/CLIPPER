"""Turn what several live sources said into a short, ranked list of what is actually hot.

Not part of upstream. No AI and no network: plain counting, so the result is predictable and can be
tested. The whole idea is cross-checking. Something that shows up in several independent places (a
search feed, a news feed, a social site, a video site) is far more likely to be genuinely hot than
something one source mentions once, and something hot that is also about the person's topic or the
video in hand is the thing worth putting in a hashtag.

Each source gives three kinds of thing (``Items``):

* ``names``: short, entity-like items ("Love Is Blind", "GTA 6"), best first,
* ``texts``: longer lines (headlines, post titles, search phrases) that can *mention* a name,
* ``tags``: hashtags people are actually using.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

STOPWORDS = frozenset(
    """a about after all also am an and any are as at be been before but by can could did do does
    for from get got had has have he her here him his how i if in into is it its just like me more
    most my new no not now of off on one only or other our out over she so some than that the their
    them then there these they this to too up us was we were what when where which who why will with
    would you your""".split()
)
# Words that are too generic to stand alone as a trend ("news", "video", "season").
GENERIC_SINGLE = frozenset(
    "news video videos today season episode episodes live trailer best top full show shows movie film game games "
    "update updates review reviews watch official new latest weekly daily world people time year week day".split()
)

MAX_NAME_CHARS = 40
TAG_MIN, TAG_MAX = 3, 24


@dataclass
class Items:
    names: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.names or self.texts or self.tags)


@dataclass
class Evidence:
    id: str
    label: str
    kind: str  # "general" (what is hot everywhere) or "niche" (searched for the person's topic or video)
    items: Items


@dataclass
class Candidate:
    name: str
    tier: int  # 0 = hot everywhere AND in the topic, 1 = in several sources, 2 = hot in one general source
    sources: list[str]  # labels of the sources that mention it
    mentions: int


@dataclass
class Ranked:
    hot: list[Candidate] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    searching: list[str] = field(default_factory=list)
    niche_phrases: list[tuple[str, int]] = field(default_factory=list)


def normalize(text: object) -> str:
    """Lowercase ASCII words separated by single spaces, so "Café  Fight!" and "cafe fight" compare equal."""
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def mentions(entity: str, haystack: str) -> bool:
    """True if the (normalized) ``entity`` appears in ``haystack`` as whole words."""
    return bool(entity) and f" {entity} " in f" {haystack} "


def usable(key: str) -> bool:
    """Is this normalized text worth treating as a trend at all?"""
    tokens = key.split()
    if not tokens or len(key) > MAX_NAME_CHARS or all(t in STOPWORDS for t in tokens):
        return False
    if all(t.isdigit() for t in tokens):
        return False
    if len(tokens) == 1 and (len(key) < 4 or key in GENERIC_SINGLE):
        return False
    return True


def tag_from(name: str) -> str | None:
    """A hashtag-shaped version of a short name ("Love Is Blind" -> "loveisblind"), or None."""
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())
    if not 1 <= len(words) <= 3:
        return None
    tag = "".join(words).lower()
    return tag if TAG_MIN <= len(tag) <= TAG_MAX and not tag.isdigit() else None


def frequent_phrases(docs: Sequence[str], *, limit: int = 8) -> list[tuple[str, int]]:
    """Phrases of one to four words that appear in several of the (normalized) lines.

    A phrase is counted once per line. A single word needs three lines, a longer phrase two. A phrase
    that starts or ends with a stopword is skipped, and so is one that sits inside a longer phrase
    that is at least as common, so the same story is not listed three times.
    """
    counts: Counter[str] = Counter()
    for doc in docs:
        tokens = doc.split()
        seen: set[str] = set()
        for n in (1, 2, 3, 4):
            for i in range(len(tokens) - n + 1):
                gram = tokens[i : i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                seen.add(" ".join(gram))
        counts.update(seen)
    keep = {p: c for p, c in counts.items() if c >= (2 if " " in p else 3) and usable(p)}
    maximal = {
        p: c for p, c in keep.items() if not any(q != p and mentions(p, q) and keep[q] >= c for q in keep)
    }
    ordered = sorted(maximal.items(), key=lambda pc: (-pc[1], -len(pc[0].split()), pc[0]))
    # Fragments of the same story ("love is blind reunion", "love is blind season 9") are listed once.
    kept: list[tuple[str, int]] = []
    seen_words: set[str] = set()
    for phrase, count in ordered:
        words = {w for w in phrase.split() if w not in STOPWORDS}
        if kept and (words <= seen_words or len(words & seen_words) >= 2):
            continue
        kept.append((phrase, count))
        seen_words |= words
    return kept[:limit]


def rank(evidence: Sequence[Evidence], *, limit: int = 12, tag_limit: int = 10) -> Ranked:
    """What is hot, best first, plus the hashtags worth offering."""
    docs: dict[str, list[str]] = {}
    squashed_tags: dict[str, set[str]] = {}
    for e in evidence:
        lines = [normalize(x) for x in (*e.items.names, *e.items.texts)]
        docs[e.id] = [x for x in lines if x]
        squashed_tags[e.id] = {normalize(t).replace(" ", "") for t in e.items.tags if normalize(t)}

    pool: dict[str, dict] = {}
    for e in evidence:
        for position, name in enumerate(e.items.names):
            key = normalize(name)
            if not usable(key):
                continue
            slot = pool.setdefault(key, {"name": name.strip(), "general": False, "position": position})
            slot["general"] = slot["general"] or e.kind == "general"
            slot["position"] = min(slot["position"], position)

    candidates: list[tuple[tuple, Candidate]] = []
    for key, slot in pool.items():
        squashed = key.replace(" ", "")
        labels, total, niche_hits = [], 0, 0
        for e in evidence:
            hits = sum(1 for line in docs[e.id] if mentions(key, line))
            if squashed in squashed_tags[e.id]:
                hits += 1
            if hits:
                labels.append(e.label)
                total += hits
                niche_hits += e.kind == "niche"
        if not labels:
            continue
        if slot["general"] and niche_hits:
            tier = 0
        elif len(labels) >= 2:
            tier = 1
        elif slot["general"]:
            tier = 2
        else:
            continue  # named by a topic-only source and nobody else confirms it: too thin to call hot
        candidates.append(
            ((tier, -len(labels), -total, slot["position"], key), Candidate(slot["name"], tier, labels, total))
        )
    candidates.sort(key=lambda pair: pair[0])
    hot = [c for _, c in candidates[:limit]]

    # hashtags: ones in use by two or more sources, then the hottest names, then other tags used for this topic
    used: Counter[str] = Counter()
    used_for_topic: Counter[str] = Counter()  # only from sources asked about this topic or video
    for e in evidence:
        for tag in {normalize(t).replace(" ", "") for t in e.items.tags}:
            if TAG_MIN <= len(tag) <= TAG_MAX and not tag.isdigit():
                used[tag] += 1
                if e.kind == "niche":
                    used_for_topic[tag] += 1
    tags: list[str] = []

    def add(tag: str | None) -> None:
        if tag and tag not in tags and len(tags) < tag_limit:
            tags.append(tag)

    for tag, count in used.most_common():
        if count >= 2:
            add(tag)
    for candidate in hot:
        if candidate.tier <= 1:  # confirmed, or tied to the topic: a lone general trend is for the model to weigh, not to tag
            add(tag_from(candidate.name))
    for tag, _ in used_for_topic.most_common():  # a general source's one-off tag has nothing to do with this topic
        add(tag)

    niche_lines = [line for e in evidence if e.kind == "niche" for line in docs[e.id]]
    searching: list[str] = []
    for e in evidence:
        if e.id.startswith("suggest"):
            for text in e.items.texts:
                if text and text.lower() not in (s.lower() for s in searching):
                    searching.append(text)
    return Ranked(
        hot=hot,
        tags=tags,
        searching=searching[:8],
        niche_phrases=frequent_phrases(niche_lines),
    )
