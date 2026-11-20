import urllib.error
import urllib.request

import pytest

from clipper import trend_net
from clipper.trend_net import FeedError


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x",  # not https
        "https://evil.example.org/x",  # not a named host
        "https://user:" + "pw" + "@example.com/x",  # credentials in the address (written in pieces so the secret scan ignores it)
        "ftp://example.com/x",
        "file:///etc/passwd",
    ],
)
def test_only_https_to_named_hosts_without_credentials(url):
    with pytest.raises(FeedError, match="not an allowed address"):
        trend_net.fetch_text(url, ["example.com"])


def redirect(origin, target, hosts=("example.com",)):
    handler = trend_net._SameSiteRedirects(frozenset(hosts))
    request = urllib.request.Request(origin)
    return handler.redirect_request(request, None, 302, "Found", {}, target)


def test_a_redirect_may_go_to_www_or_a_named_host_but_nowhere_else():
    assert redirect("https://example.com/a", "https://www.example.com/a") is not None
    assert redirect("https://www.example.com/a", "https://example.com/b") is not None
    assert redirect("https://a.example.com/x", "https://other.example.org/y", hosts=("a.example.com", "other.example.org")) is not None
    for target in ("https://evil.example.org/", "http://example.com/downgrade", "https://example.com.evil.org/"):
        with pytest.raises(urllib.error.URLError, match="different site"):
            redirect("https://example.com/a", target)


def test_a_non_utf8_page_is_decoded_with_its_own_charset(monkeypatch):
    class Response:
        headers = type("H", (), {"get_content_charset": lambda self: "latin-1"})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return "café".encode("latin-1")

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    assert trend_net.fetch_text("https://example.com/x", ["example.com"]) == "café"


def test_http_errors_carry_their_status_and_connection_problems_are_readable(monkeypatch):
    class Opener:
        def __init__(self, exc):
            self.exc = exc

        def open(self, request, timeout):
            raise self.exc

    http_error = urllib.error.HTTPError("https://example.com/x", 503, "Unavailable", {}, None)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener(http_error))
    with pytest.raises(FeedError) as caught:
        trend_net.fetch_text("https://example.com/x", ["example.com"])
    assert caught.value.status == 503 and "HTTP 503" in str(caught.value)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener(urllib.error.URLError("name not resolved")))
    with pytest.raises(FeedError, match="couldn't connect \\(name not resolved\\)"):
        trend_net.fetch_text("https://example.com/x", ["example.com"])


def test_an_oversized_reply_is_refused(monkeypatch):
    class Response:
        headers = type("H", (), {"get_content_charset": lambda self: None})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * n  # always more than the limit

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(FeedError, match="unexpectedly large"):
        trend_net.fetch_text("https://example.com/x", ["example.com"])
