from clipper import trend_rank as rank
from clipper.trend_rank import Evidence, Items


def ev(id_, kind, *, names=(), texts=(), tags=()):
    return Evidence(id_, id_.replace("_", " ").title(), kind, Items(list(names), list(texts), list(tags)))


def test_normalize_folds_case_accents_and_punctuation():
    assert rank.normalize("  Café  FIGHT!! ") == "cafe fight"
    assert rank.normalize("Love Is Blind: S8") == "love is blind s8"


def test_mentions_needs_whole_words():
    assert rank.mentions("love is blind", "the love is blind reunion")
    assert not rank.mentions("love", "glove compartment")
    assert not rank.mentions("", "anything")


def test_usable_rejects_generic_or_tiny_things():
    for bad in ("", "the", "of the", "2026", "news", "ok", "x" * 41):
        assert not rank.usable(bad), bad
    for good in ("love is blind", "gta 6", "chelsea"):
        assert rank.usable(good), good


def test_tag_from_makes_a_hashtag_shaped_word():
    assert rank.tag_from("Love Is Blind") == "loveisblind"
    assert rank.tag_from("GTA 6") == "gta6"
    assert rank.tag_from("A very long name with many words") is None  # more than three words
    assert rank.tag_from("12345") is None and rank.tag_from("ab") is None


def test_frequent_phrases_keeps_what_repeats_and_drops_nested_repeats():
    docs = [rank.normalize(t) for t in [
        "Love Is Blind reunion goes wild",
        "Love Is Blind reunion: the fight",
        "Why the Love Is Blind reunion blew up",
        "Weather today is sunny",
    ]]
    found = dict(rank.frequent_phrases(docs))
    assert found["love is blind reunion"] == 3  # the longest phrase that is as common as its parts
    assert "love is blind" not in found and "blind reunion" not in found and "reunion" not in found  # nested in it
    assert "weather today" not in found  # once only


def test_a_single_word_needs_three_lines():
    docs = ["chelsea said it", "chelsea left", "something else"]
    assert dict(rank.frequent_phrases(docs)) == {}
    assert "chelsea" in dict(rank.frequent_phrases(docs + ["chelsea again"]))


def test_hot_everywhere_and_in_your_topic_ranks_first():
    evidence = [
        ev("google_trends", "general", names=["Weather Today", "Love Is Blind", "Chiefs Bills"]),
        ev("wikipedia", "general", names=["Chiefs Bills", "Some Article"]),
        ev("google_news", "niche", texts=["Love Is Blind reunion drama", "Love Is Blind season 9 cast"]),
    ]
    ranked = rank.rank(evidence)
    names = [c.name for c in ranked.hot]
    assert names[0] == "Love Is Blind" and ranked.hot[0].tier == 0
    assert ranked.hot[0].sources == ["Google Trends", "Google News"]
    chiefs = next(c for c in ranked.hot if c.name == "Chiefs Bills")
    assert chiefs.tier == 1  # two independent general sources agree, but it is not about the topic
    assert names.index("Chiefs Bills") < names.index("Weather Today")  # agreement beats a single mention


def test_a_name_only_a_topic_source_gives_is_too_thin_to_call_hot():
    ranked = rank.rank([ev("bing_news", "niche", names=["Random Person"])])
    assert ranked.hot == []


def test_an_explicit_tag_confirms_a_name_even_without_spaces():
    evidence = [
        ev("google_trends", "general", names=["Love Is Blind"]),
        ev("youtube", "niche", tags=["loveisblind"]),
    ]
    assert rank.rank(evidence).hot[0].tier == 0


def test_tags_come_from_agreement_first_then_hot_names_and_one_off_general_tags_are_not_padding():
    evidence = [
        ev("google_trends", "general", names=["Love Is Blind"]),
        ev("mastodon", "general", tags=["caturday", "loveisblind"]),
        ev("youtube", "niche", tags=["loveisblind", "reunion", "drama"]),
    ]
    tags = rank.rank(evidence).tags
    assert tags[0] == "loveisblind"  # used by two sources
    assert "reunion" in tags and "drama" in tags  # in use for this topic
    assert "caturday" not in tags  # a general source's one-off tag has nothing to do with this topic


def test_tag_limit_and_search_phrases():
    many = [f"topic{i}word" for i in range(30)]
    ranked = rank.rank([ev("youtube", "niche", tags=many + many)], tag_limit=5)
    assert len(ranked.tags) == 5
    phrases = [rank.normalize(p) and p for p in ["love is blind reunion", "Love Is Blind Reunion", "love is blind cast"]]
    searching = rank.rank([ev("suggest_google", "niche", texts=phrases)]).searching
    assert searching == ["love is blind reunion", "love is blind cast"]  # the duplicate (any case) is dropped


def test_fragments_of_one_story_are_listed_once():
    docs = [rank.normalize(x) for x in [
        "Love Is Blind reunion fight", "Love Is Blind reunion fight", "Love Is Blind reunion fight",
        "Love Is Blind season 9 cast", "Love Is Blind season 9 cast",
        "Chiefs beat Bills", "Chiefs beat Bills",
    ]]
    found = [p for p, _ in rank.frequent_phrases(docs)]
    assert found[0] == "love is blind"  # the name that is in every line beats the longer, rarer fragments
    assert "love is blind reunion fight" not in found and "love is blind season 9 cast" not in found
    assert "chiefs beat bills" in found  # a different story is kept


def test_a_lone_general_trend_is_shown_to_the_model_but_not_offered_as_a_tag():
    evidence = [
        ev("google_trends", "general", names=["Weather Today", "Love Is Blind"]),
        ev("google_news", "niche", texts=["Love Is Blind reunion"]),
    ]
    ranked = rank.rank(evidence)
    assert "Weather Today" in [c.name for c in ranked.hot]
    assert "weathertoday" not in ranked.tags and "loveisblind" in ranked.tags
