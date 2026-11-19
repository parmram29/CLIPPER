import pytest

from clipper import trend_sites as sites
from clipper.trend_net import FeedError

PAGE = """<html><head><title>Trending hashtags</title><style>.x { color: #fff }</style>
<script>var a = "#hiddenintag"; </script></head>
<body><nav><a>#menutag menu</a></nav>
<h1>Top hashtags this week</h1>
<ul><li>#LoveIsBlind is up 40%</li><li>#reunion</li><li>#LoveIsBlind again</li></ul>
<p>Don&#39;t miss #DramaAlert today &amp; more.</p>
<footer>#footertag</footer></body></html>"""


def test_the_file_is_read_line_by_line_with_labels_and_comments():
    text = """// my sites
https://example.com/trending | Example trends
   https://tags.example.org/{tag}/
https://search.example.net/?q={query} | Search site

// the next ones cannot be used
http://insecure.example.com/
https://localhost/x
https://192.168.1.5/x
""" + "https://user:" + "pw" + """@example.com/x
https://nodot/x
"""
    found, problems = sites.parse_sites(text)
    assert [(s.url, s.label, s.host) for s in found] == [
        ("https://example.com/trending", "Example trends", "example.com"),
        ("https://tags.example.org/{tag}/", "tags.example.org", "tags.example.org"),
        ("https://search.example.net/?q={query}", "Search site", "search.example.net"),
    ]
    assert [s.seeded for s in found] == [False, True, True]
    assert len(problems) == 5 and all("https://" in p and p.startswith("line ") for p in problems)


def test_placeholders_become_the_topic_or_the_site_is_skipped():
    plain, tagged, queried = sites.parse_sites(
        "https://a.example.com/x\nhttps://b.example.com/{tag}/\nhttps://c.example.com/?q={query}\n"
    )[0]
    assert sites.expand(plain, None) == "https://a.example.com/x"
    assert sites.expand(tagged, "Love Is Blind!") == "https://b.example.com/loveisblind/"
    assert sites.expand(queried, "love is blind") == "https://c.example.com/?q=love+is+blind"
    assert sites.expand(tagged, None) is None and sites.expand(queried, "  ") is None
    assert sites.expand(tagged, "!!!") is None  # nothing left to make a tag from


def test_the_reader_finds_visible_hashtags_and_headlines_and_ignores_scripts_menus_and_styles():
    items = sites.extract_signals(PAGE)
    assert items.tags == ["loveisblind", "reunion", "dramaalert"]
    assert "hiddenintag" not in items.tags and "menutag" not in items.tags and "footertag" not in items.tags and "fff" not in items.tags
    assert "Top hashtags this week" in items.texts and "#LoveIsBlind is up 40%" in items.texts
    assert items.names == []  # a site can add tags and supporting text, never an invented "hot" name


def test_a_broken_page_gives_what_could_be_read():
    assert sites.extract_signals("<ul><li>#goodtag<li>#other <b").tags[:1] == ["goodtag"]
    assert sites.extract_signals("").is_empty()


def robots(body=None, error=None):
    def fetch(url):
        assert url.endswith("/robots.txt")
        if error:
            raise error
        return body

    return fetch


def test_robots_txt_is_respected():
    page = "https://example.com/trending/hot"
    assert sites.robots_allows(page, robots("User-agent: *\nDisallow: /trending/"))[0] is False
    assert sites.robots_allows(page, robots("User-agent: *\nDisallow: /private/"))[0] is True
    assert sites.robots_allows(page, robots("User-agent: CLIPPER\nDisallow: /\nUser-agent: *\nAllow: /"))[0] is False
    allowed, note = sites.robots_allows(page, robots("User-agent: *\nDisallow: /"))
    assert not allowed and "robots.txt asks robots not to read" in note


def test_no_robots_file_means_allowed_but_a_server_error_means_skip():
    page = "https://example.com/x"
    assert sites.robots_allows(page, robots(error=FeedError("HTTP 404", status=404)))[0] is True
    assert sites.robots_allows(page, robots(error=FeedError("HTTP 503", status=503)))[0] is False
    assert sites.robots_allows(page, robots(error=FeedError("couldn't connect")))[0] is False


def test_the_template_is_made_once_and_never_overwritten(tmp_path):
    path = tmp_path / "trend_sites.txt"
    assert sites.init_template(path) == path and "Websites CLIPPER reads" in path.read_text(encoding="utf-8")
    path.write_text("https://mine.example.com/\n", encoding="utf-8")
    sites.init_template(path)
    assert path.read_text(encoding="utf-8") == "https://mine.example.com/\n"
    found, problems = sites.load_sites(path)
    assert [s.url for s in found] == ["https://mine.example.com/"] and problems == []
    assert sites.load_sites(tmp_path / "missing.txt") == ([], [])


def test_the_shipped_template_has_no_active_sites_until_you_turn_one_on():
    assert sites.parse_sites(sites.TEMPLATE) == ([], [])
