"""The one place the live trend code touches the internet.

Not part of upstream. It reads a page as text and nothing else: https only, only from hosts the
caller names, a size limit, a short timeout, and no following of a redirect to some other site. It
sends no cookies and no login, and says plainly what it is in the User-Agent.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
# Used only through fetch_text(): https, named hosts, no cookies.
import urllib.request  # nosec B404
from typing import Iterable

TIMEOUT = 8  # seconds per page
MAX_BYTES = 1_000_000
USER_AGENT = "CLIPPER/0.1 (personal clip-making tool; https://github.com/parmram29/CLIPPER)"
ROBOTS_NAME = "CLIPPER"


class FeedError(RuntimeError):
    """A page could not be read. The message is short and safe to show a person."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def bare(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


class _SameSiteRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to https and only to a named host or the same site (www or not)."""

    def __init__(self, hosts: frozenset[str]) -> None:
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urllib.parse.urlsplit(newurl)
        origin = urllib.parse.urlsplit(req.full_url).hostname or ""
        host = target.hostname or ""
        if target.scheme != "https" or not (host in self.hosts or bare(host) == bare(origin)):
            raise urllib.error.URLError("it redirected to a different site")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(url: str, hosts: Iterable[str], timeout: float = TIMEOUT) -> str:
    """GET ``url`` as text. ``hosts`` is the list of sites this call may talk to."""
    allowed = frozenset(hosts)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in allowed or parts.username or parts.password:
        raise FeedError(f"not an allowed address ({parts.hostname})")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    opener = urllib.request.build_opener(_SameSiteRedirects(allowed))
    try:
        # https only, and only the named hosts (checked above).
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            data = response.read(MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise FeedError(f"the site answered HTTP {exc.code}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FeedError(f"couldn't connect ({str(reason)[:80] or 'no answer'})") from exc
    if len(data) > MAX_BYTES:
        raise FeedError("the reply was unexpectedly large")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")
