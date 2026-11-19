"""The small web server behind the app (standard library only).

It runs on the person's own computer, but a web page they have open in another tab could still try to
talk to it, so every request is treated as possibly hostile. In order:

1. It listens on 127.0.0.1 only, so nothing else on the network can reach it.
2. The ``Host`` header must name this server, which stops "DNS rebinding" (a hostile site pointing
   its own name at 127.0.0.1 to get past the browser's same-origin rules).
3. A random token, made fresh on every start, travels in the address the launcher opens. The server
   swaps it for a ``SameSite=Strict`` ``HttpOnly`` cookie, which is what every later request needs.
4. Anything that changes something (POST) also needs a custom header, an ``Origin`` that is this
   server (or none), a JSON body, and a size under 64 KiB. No CORS headers are ever sent.
5. A clip video is found by (session id, clip number), never by a path from the page.
6. A strict Content-Security-Policy: only this server's own scripts and styles run.
"""

from __future__ import annotations

import errno
import hmac
import json
import os
import re
import secrets
import sys
import threading
import traceback
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from .. import config, export, trend_feeds
from . import commands, jobs as jobs_module, sessions

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/icon.svg": ("icon.svg", "image/svg+xml"),
}
COOKIE_NAME = "clipper_session"
MAX_BODY = 64 * 1024
CHUNK = 256 * 1024
FIRST_PORT = 8765
PORT_TRIES = 20

SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    (
        "Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
)

LOCKED_PAGE = (
    "<!doctype html><meta charset=utf-8><title>CLIPPER</title>"
    "<body><h1>CLIPPER is running</h1>"
    "<p>But this browser tab wasn't opened by the launcher, so it isn't allowed in.</p>"
    "<p>Close this tab and open CLIPPER again (double-click <b>CLIPPER.command</b>).</p></body>"
)

RE_SESSION = re.compile(r"/api/sessions/([0-9]{8}_[0-9]{6})")
RE_MEDIA = re.compile(r"/media/([0-9]{8}_[0-9]{6})/([0-9]{1,3})")
RE_JOB = re.compile(r"/api/jobs/([A-Za-z0-9_-]{6,40})")
RE_JOB_CANCEL = re.compile(r"/api/jobs/([A-Za-z0-9_-]{6,40})/cancel")
DOCTOR_ROW = re.compile(r"^\s*\[(ok|WARN|FAIL)\s*\]\s*(.*)$")
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeNotSatisfiable(ValueError):
    pass


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The (first, last) byte to send for a ``Range`` header, or None to send the whole file.

    Browsers need this to play and scrub a video. Raises ``RangeNotSatisfiable`` when the range
    starts past the end of the file. Anything it can't make sense of is ignored, as the spec allows.
    """
    if not header or size <= 0:
        return None
    match = RANGE.fullmatch(header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        return None
    first, last = match.group(1), match.group(2)
    if not first:  # "-500": the last 500 bytes
        length = int(last)
        if length == 0:
            raise RangeNotSatisfiable
        return max(0, size - length), size - 1
    start = int(first)
    if start >= size:
        raise RangeNotSatisfiable
    end = size - 1 if not last else min(int(last), size - 1)
    if end < start:
        return None
    return start, end


def _env_with_dotenv(project_dir: Path) -> dict[str, str]:
    """The real environment over the project's ``.env``. The server reads ``.env`` itself, fresh,
    instead of loading it into its own environment: each job reloads the file, so a key typed into
    ``.env`` while the app is open is picked up by the next job."""
    values: dict[str, str] = {}
    if not os.environ.get("CLIPPER_SKIP_DOTENV"):
        try:
            from dotenv import dotenv_values

            values = {k: v for k, v in dotenv_values(project_dir / ".env").items() if v is not None}
        except ImportError:
            pass
    return {**values, **os.environ}


def trend_status(env: Mapping[str, str]) -> dict[str, Any]:
    """Whether live trends are on, and how the last read of them went (from local files only)."""
    enabled = (env.get("CLIPPER_LIVE_TRENDS") or "1").strip().lower() not in ("0", "false", "no", "off")
    niche = (env.get("CLIPPER_NICHE") or "").strip() or None
    region = (env.get("CLIPPER_REGION") or "").strip() or None
    return {"enabled": enabled, **trend_feeds.status(niche, region)}


class ClipperServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(
        self,
        port: int,
        *,
        project_dir: str | Path,
        out_dir: str | Path,
        token: str | None = None,
        base_command: Sequence[str] | None = None,
    ) -> None:
        self._stop = threading.Event()  # first: server_close() runs inside super().__init__ if the port is taken
        self.project_dir = Path(project_dir).resolve()
        out = Path(out_dir)
        self.out_dir = (out if out.is_absolute() else self.project_dir / out).resolve()
        self.token = token or secrets.token_urlsafe(32)
        self.jobs = jobs_module.JobManager(self.project_dir, base_command=base_command)
        super().__init__(("127.0.0.1", port), Handler)
        self.port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {f"http://{host}" for host in self.allowed_hosts}

    def start_trend_refresher(self, first_after: float = 20.0, every: float = 1800.0) -> threading.Thread:
        """While the app is open, keep the live trends fresh on their own: check every half hour and read
        the sources again when the saved copy is out of date. It is the same command as `clipper trends --live`."""

        def loop() -> None:
            wait = first_after
            while not self._stop.wait(wait):
                wait = every
                try:
                    status = trend_status(_env_with_dotenv(self.project_dir))
                    if status["enabled"] and status["due"] and not status["failure"]:
                        self.jobs.run_quick(["trends", "--live"], timeout=90)
                except Exception:  # noqa: BLE001 - a background refresh must never take the app down
                    traceback.print_exc(file=sys.stderr)

        thread = threading.Thread(target=loop, name="trend-refresher", daemon=True)
        thread.start()
        return thread

    def server_close(self) -> None:
        self._stop.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: ClipperServer
    protocol_version = "HTTP/1.1"
    server_version = "CLIPPER"
    sys_version = ""
    _unread_body = False  # a POST whose body has not been read yet

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server API
        return  # the Terminal window is for the person, not a request log

    # -- plumbing ----------------------------------------------------------------------------

    def _head(self, status: int, content_type: str, length: int, extra: Sequence[tuple[str, str]] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        for name, value in (*SECURITY_HEADERS, *extra):
            self.send_header(name, value)
        if self._unread_body:
            # We are answering without having read the request's body. On a kept-alive connection those
            # bytes would be taken for the start of the next request, so close the connection instead.
            self.send_header("Connection", "close")
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str, extra: Sequence[tuple[str, str]] = ()) -> None:
        self._head(status, content_type, len(body), extra)
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- who is asking -------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.server.allowed_hosts

    def _token_matches(self, given: str) -> bool:
        return hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8"))

    def _authorised(self) -> bool:
        header = self.headers.get("Cookie")
        if not header:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(header)
        except CookieError:
            return False
        morsel = jar.get(COOKIE_NAME)
        return morsel is not None and self._token_matches(morsel.value)

    def _post_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            return False
        if self.headers.get("X-Requested-With") != "clipper":
            return False
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() == "application/json"

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._error(411, "A length is required.")
            return None
        if length < 0 or length > MAX_BODY:
            self._error(413, "That is too big.")
            return None
        raw = self.rfile.read(length)
        self._unread_body = False
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            self._error(400, "That wasn't understood.")
            return None
        self._unread_body = False
        if not isinstance(data, dict):
            self._error(400, "That wasn't understood.")
            return None
        return data

    # -- entry points --------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        self._unread_body = method == "POST"
        try:
            if self.path.startswith("//") or "\x00" in self.path:
                return self._error(400, "Bad request.")
            parts = urlsplit(self.path)
            path = parts.path
            if not self._host_ok():
                return self._error(403, "Wrong address.")
            if method == "GET" and path == "/":
                return self._page(parts.query)
            if not self._authorised():
                return self._error(403, "Open CLIPPER from its launcher to use it.")
            if method == "POST":
                if not self._post_ok():
                    return self._error(403, "That request was not allowed.")
                return self._post(path)
            return self._get(path, parts.query)
        except (BrokenPipeError, ConnectionResetError):
            return  # the browser went away (for example while scrubbing a video)
        except Exception:  # noqa: BLE001 - never leak a traceback to the page; show it in the Terminal
            traceback.print_exc(file=sys.stderr)
            try:
                self._error(500, "Something went wrong inside CLIPPER. The Terminal window has details.")
            except OSError:
                pass

    # -- pages and files -----------------------------------------------------------------------

    def _page(self, query: str) -> None:
        given = (parse_qs(query).get("token") or [""])[0]
        if given:
            if not self._token_matches(given):
                return self._send(403, LOCKED_PAGE.encode(), "text/html; charset=utf-8")
            cookie = f"{COOKIE_NAME}={self.server.token}; Path=/; HttpOnly; SameSite=Strict"
            return self._head(303, "text/plain", 0, (("Location", "/"), ("Set-Cookie", cookie)))
        if not self._authorised():
            return self._send(403, LOCKED_PAGE.encode(), "text/html; charset=utf-8")
        self._static("index.html", "text/html; charset=utf-8")

    def _static(self, name: str, content_type: str) -> None:
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            return self._error(404, "Not found.")
        self._send(200, body, content_type)

    def _serve_clip(self, path: Path) -> None:
        size = path.stat().st_size
        try:
            span = parse_range(self.headers.get("Range"), size)
        except RangeNotSatisfiable:
            return self._head(416, "text/plain", 0, (("Content-Range", f"bytes */{size}"),))
        start, end = span if span else (0, size - 1)
        length = max(0, end - start + 1)
        extra = [("Accept-Ranges", "bytes")]
        if span:
            extra.append(("Content-Range", f"bytes {start}-{end}/{size}"))
        self._head(206 if span else 200, "video/mp4", length, extra)
        with path.open("rb") as handle:
            handle.seek(start)
            left = length
            while left > 0:
                chunk = handle.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # -- GET -----------------------------------------------------------------------------------

    def _get(self, path: str, query: str) -> None:
        if path in STATIC_FILES:
            return self._static(*STATIC_FILES[path])
        if path == "/api/state":
            return self._json(200, self._state())
        if path == "/api/sessions":
            return self._json(200, {"sessions": sessions.list_sessions(self.server.out_dir)})
        if match := RE_SESSION.fullmatch(path):
            data = sessions.read_session(self.server.out_dir, match.group(1))
            return self._json(200, data) if data else self._error(404, "I can't find that session.")
        if match := RE_MEDIA.fullmatch(path):
            clip = sessions.master_path(self.server.out_dir, match.group(1), int(match.group(2)))
            return self._serve_clip(clip) if clip else self._error(404, "That clip isn't made yet.")
        if match := RE_JOB.fullmatch(path):
            job = self.server.jobs.get(match.group(1))
            if job is None:
                return self._error(404, "I don't know that job.")
            try:
                since = max(0, int((parse_qs(query).get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            return self._json(200, self.server.jobs.snapshot(job, since))
        self._error(404, "Not found.")

    def _state(self) -> dict[str, Any]:
        env = _env_with_dotenv(self.server.project_dir)
        ai: dict[str, Any] = {"configured": False, "model": "", "hint": ""}
        try:
            cfg = config.load_ai_config(env)
            ai = {"configured": True, "model": cfg.model, "hint": cfg.masked_key}
        except config.ConfigError as exc:
            ai["problem"] = str(exc)
        cookies = Path(env.get("CLIPPER_COOKIES") or "cookies.txt")
        if not cookies.is_absolute():
            cookies = self.server.project_dir / cookies
        busy = self.server.jobs.running()
        niche = (env.get("CLIPPER_NICHE") or "").strip()
        return {
            "ai": ai,
            "cookies": {"found": cookies.is_file()},
            "niche": niche,
            "busy": busy.id if busy else None,
            "styles": list(commands.STYLES),
            "trends": trend_status(env),
        }

    # -- POST ----------------------------------------------------------------------------------

    def _post(self, path: str) -> None:
        body = self._body()
        if body is None:
            return
        if path == "/api/jobs":
            return self._start_job(body)
        if match := RE_JOB_CANCEL.fullmatch(path):
            return self._json(200, {"cancelled": self.server.jobs.cancel(match.group(1))})
        if path == "/api/open":
            return self._open(body)
        if path == "/api/doctor":
            return self._doctor(body)
        if path == "/api/trends/check":
            return self._check_trends()
        self._error(404, "Not found.")

    def _start_job(self, body: Mapping[str, Any]) -> None:
        kind = str(body.get("kind") or "")
        try:
            args = commands.build_command(kind, body, self.server.out_dir)
        except commands.RequestError as exc:
            return self._error(400, str(exc))
        session = str(body.get("session")) if kind in ("process", "export") else None
        try:
            job = self.server.jobs.start(kind, args, session=session)
        except jobs_module.JobBusy:
            return self._error(409, "CLIPPER is still working on another video. Wait for it, or cancel it first.")
        self._json(201, {"id": job.id})

    def _open(self, body: Mapping[str, Any]) -> None:
        what = body.get("what")
        try:
            if what == "project":
                target = self.server.project_dir
            elif what in ("clips", "export"):
                folder = commands.session_path(self.server.out_dir, body.get("session"))
                target = folder / "clips" if what == "clips" else export.default_export_dir(folder)
            else:
                return self._error(400, "I don't know what to open.")
        except commands.RequestError as exc:
            return self._error(400, str(exc))
        if not target.is_dir():
            return self._error(404, "There's nothing there yet.")
        self._json(200, {"opened": export.reveal(target), "path": str(target)})

    def _check_trends(self) -> None:
        """Read the live trend sources right now and say how each one went (the same as `clipper trends --live`)."""
        code, output = self.server.jobs.run_quick(["trends", "--live"], timeout=90)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        self._json(200, {"ok": code == 0, "lines": lines[-40:], "status": trend_status(_env_with_dotenv(self.server.project_dir))})

    def _doctor(self, body: Mapping[str, Any]) -> None:
        args = ["doctor", *(["--check-ai"] if body.get("ai") is True else [])]
        code, output = self.server.jobs.run_quick(args)
        rows = []
        for line in output.splitlines():
            if match := DOCTOR_ROW.match(line):
                rows.append({"level": match.group(1).lower(), "message": match.group(2).strip()})
        self._json(200, {"ok": code == 0, "rows": rows, "output": output[-4000:]})


def serve(
    project_dir: str | Path,
    out_dir: str | Path,
    *,
    port: int = FIRST_PORT,
    open_browser: bool = True,
    base_command: Sequence[str] | None = None,
) -> int:
    """Run the app until Ctrl+C. Returns the exit status."""
    server: ClipperServer | None = None
    for candidate in range(port, port + (PORT_TRIES if port else 1)):
        try:
            server = ClipperServer(candidate, project_dir=project_dir, out_dir=out_dir, base_command=base_command)
            break
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    if server is None:
        print(f"error: couldn't find a free port starting at {port}.", file=sys.stderr)
        return 1

    address = f"http://127.0.0.1:{server.port}/"
    entry = f"{address}?token={server.token}"
    print("CLIPPER is running.")
    print(f"   Address: {address}")
    print("   Leave this window open while you use it. Press Ctrl+C here (or close the window) to stop.")
    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(entry)
        except webbrowser.Error:
            opened = False
    if not opened:
        print(f"   Open this in your browser: {entry}")
    server.start_trend_refresher()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CLIPPER…")
    finally:
        server.jobs.shutdown()
        server.server_close()
    return 0
