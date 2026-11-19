import http.client
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from clipper import export, trend_feeds
from clipper.gui import server as srv
from clipper.gui.server import ClipperServer, RangeNotSatisfiable, parse_range

STUB = Path(__file__).parent / "gui_fake_cli.py"
VIDEO = "https://www.youtube.com/watch?v=abcdefghijk"


class App:
    def __init__(self, server, home):
        self.server, self.home = server, home
        self.port, self.token = server.port, server.token

    def request(self, method, path, body=None, *, cookie=True, headers=None, host=None, raw=None, post_ok=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = {"Host": host or f"127.0.0.1:{self.port}"}
        if cookie is True:
            sent["Cookie"] = f"{srv.COOKIE_NAME}={self.token}"
        elif cookie:
            sent["Cookie"] = f"{srv.COOKIE_NAME}={cookie}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
        if method == "POST" and post_ok:
            sent.update({"X-Requested-With": "clipper", "Content-Type": "application/json"})
        sent.update(headers or {})
        conn.request(method, path, body=data, headers=sent)
        response = conn.getresponse()
        payload = response.read()
        result = (response.status, {k.lower(): v for k, v in response.getheaders()}, payload)
        conn.close()
        return result

    def get(self, path, **kw):
        status, headers, payload = self.request("GET", path, **kw)
        return status, (json.loads(payload) if payload and headers.get("content-type", "").startswith("application/json") else payload)

    def post(self, path, body=None, **kw):
        status, headers, payload = self.request("POST", path, {} if body is None else body, **kw)
        return status, (json.loads(payload) if payload and headers.get("content-type", "").startswith("application/json") else payload)

    def run_job(self, body, timeout=60):
        status, data = self.post("/api/jobs", body)
        assert status == 201, data
        return self.wait(data["id"], timeout)

    def wait(self, job_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, snap = self.get(f"/api/jobs/{job_id}")
            if snap["status"] != "running":
                return snap
            time.sleep(0.1)
        raise AssertionError("job did not finish")


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CLIPPER_API_KEY", "test-key-abcdefgh")
    monkeypatch.setenv("FAKE_DELAY", "0")
    server = ClipperServer(0, project_dir=project, out_dir="out", base_command=[sys.executable, "-u", str(STUB)])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield App(server, home)
    server.shutdown()
    server.jobs.shutdown()
    server.server_close()
    thread.join(timeout=5)


# --- who may talk to it -----------------------------------------------------------------------------------


def test_it_listens_on_this_computer_only(app):
    assert app.server.server_address[0] == "127.0.0.1"


def test_without_the_launchers_token_the_page_stays_locked_and_reveals_nothing(app):
    status, _, body = app.request("GET", "/", cookie=False)
    assert status == 403 and b"CLIPPER is running" in body and app.token.encode() not in body
    assert app.request("GET", "/?token=wrong", cookie=False)[0] == 403
    assert app.request("GET", "/?token=", cookie=False)[0] == 403


def test_the_token_is_swapped_for_a_strict_httponly_cookie_and_then_the_page_loads(app):
    status, headers, _ = app.request("GET", f"/?token={app.token}", cookie=False)
    assert status == 303 and headers["location"] == "/"
    cookie = headers["set-cookie"]
    assert cookie.startswith(f"{srv.COOKIE_NAME}={app.token}") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    status, headers, body = app.request("GET", "/")
    assert status == 200 and b"<title>CLIPPER</title>" in body and headers["content-type"].startswith("text/html")


def test_every_answer_carries_the_security_headers_and_no_cors_permission(app):
    for path in ("/", "/api/state", "/static/app.js", "/nope"):
        _, headers, _ = app.request("GET", path)
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert headers["cache-control"] == "no-store" and headers["referrer-policy"] == "no-referrer"
        policy = headers["content-security-policy"]
        assert "default-src 'none'" in policy and "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
        assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
        assert not any(name.startswith("access-control-") for name in headers)


@pytest.mark.parametrize("path", ["/api/state", "/api/sessions", "/static/app.js", "/media/20261105_130426/0", "/api/jobs/abcdefgh"])
def test_every_page_and_api_needs_the_cookie(app, path):
    assert app.request("GET", path, cookie=False)[0] == 403
    assert app.request("GET", path, cookie="not-the-token")[0] == 403
    assert app.request("GET", path, cookie="x" * 5000)[0] == 403
    assert app.request("GET", path, headers={"Cookie": "this is ; not = a cookie"}, cookie=False)[0] == 403


def test_a_page_pointing_its_own_name_at_this_computer_is_refused(app):  # "DNS rebinding"
    for host in ("evil.example", f"evil.example:{app.port}", f"127.0.0.1.evil.example:{app.port}", "127.0.0.1", f"127.0.0.1:{app.port + 1}"):
        assert app.request("GET", "/api/state", host=host)[0] == 403, host
        assert app.request("GET", f"/?token={app.token}", host=host, cookie=False)[0] == 403, host
    assert app.request("GET", "/api/state", host=f"localhost:{app.port}")[0] == 200


@pytest.mark.parametrize(
    "kw",
    [
        dict(headers={"X-Requested-With": "other"}),
        dict(headers={"Origin": "http://evil.example"}),
        dict(headers={"Origin": "null"}),
        dict(headers={"Origin": "https://127.0.0.1"}),
        dict(headers={"Content-Type": "text/plain"}),
        dict(headers={"Content-Type": "application/x-www-form-urlencoded"}),
        dict(post_ok=False),  # no custom header, no JSON content type
    ],
)
def test_changing_anything_needs_a_same_origin_json_request_with_the_custom_header(app, kw):
    status, data = app.post("/api/open", {"what": "project"}, **kw)
    assert status == 403, kw


def test_a_refused_post_closes_the_connection_so_its_unread_body_is_never_taken_for_a_request(app, capfd):
    status, headers, _ = app.request("POST", "/api/open", {"what": "project"}, headers={"X-Requested-With": "other"})
    assert status == 403 and headers["connection"] == "close"
    status, headers, _ = app.request("POST", "/api/jobs", raw=b"x" * (srv.MAX_BODY + 1))
    assert status == 413 and headers["connection"] == "close"
    # a body that was read and answered normally keeps the connection usable
    status, headers, _ = app.request("POST", "/api/open", {"what": "nope"})
    assert status == 400 and headers.get("connection") != "close"
    time.sleep(0.2)
    assert "Traceback" not in capfd.readouterr().err  # and the server printed no error about any of it


def test_a_same_origin_post_with_the_right_headers_is_accepted(app):
    ok = {"Origin": f"http://127.0.0.1:{app.port}"}
    assert app.post("/api/open", {"what": "project"}, headers=ok)[0] in (200, 404)
    assert app.post("/api/open", {"what": "project"}, headers={"Origin": f"http://localhost:{app.port}"})[0] in (200, 404)


def test_bad_bodies_are_refused_without_a_crash(app):
    assert app.request("POST", "/api/jobs", raw=b"{not json")[0] == 400
    assert app.request("POST", "/api/jobs", raw=b"[1, 2]")[0] == 400
    assert app.request("POST", "/api/jobs", raw=b"x" * (srv.MAX_BODY + 1))[0] == 413
    assert app.request("POST", "/api/jobs", raw=b"{}", headers={"Content-Length": "abc"})[0] in (400, 411)
    assert app.get("/api/state")[0] == 200  # and it is still alive


def test_other_methods_and_preflight_requests_are_not_answered(app):
    for method in ("OPTIONS", "PUT", "DELETE", "PATCH", "HEAD"):
        status, headers, _ = app.request(method, "/api/state")
        assert status >= 400 and "access-control-allow-origin" not in headers, method


@pytest.mark.parametrize(
    "path",
    ["/static/../server.py", "/static/%2e%2e/server.py", "/static/app.js/../../server.py", "/media/../../etc/passwd",
     "/media/20261105_130426/../../../etc/passwd", "/media/20261105_130426/0%00", "/media/20261105_130426/-1",
     "/api/sessions/..%2f..%2fetc", "//evil.example/api/state", "/static/", "/static/server.py", "/api/jobs/../state"],
)
def test_paths_cannot_escape_what_is_served(app, path):
    status, _, body = app.request("GET", path)
    assert status in (400, 404), (path, status)
    assert b"root:" not in body and b"import" not in body


# --- state, sessions, files ----------------------------------------------------------------------------------


def test_state_says_what_is_set_up_without_ever_sending_the_key(app):
    status, state = app.get("/api/state")
    assert status == 200 and state["ai"] == {"configured": True, "model": "qwen-plus", "hint": "...efgh"}
    assert b"test-key-abcdefgh" not in json.dumps(state).encode()
    assert state["cookies"]["found"] is False and state["busy"] is None and state["styles"] == ["drama", "wild", "podcast"]
    (app.server.project_dir / "cookies.txt").write_text("x", encoding="utf-8")
    assert app.get("/api/state")[1]["cookies"]["found"] is True


def test_state_reads_the_env_file_fresh_every_time(app, monkeypatch):
    monkeypatch.delenv("CLIPPER_API_KEY")
    monkeypatch.delenv("CLIPPER_SKIP_DOTENV")
    assert app.get("/api/state")[1]["ai"]["configured"] is False
    (app.server.project_dir / ".env").write_text("CLIPPER_API_KEY=from-the-env-file-1234\nCLIPPER_NICHE=gta 6\n", encoding="utf-8")
    state = app.get("/api/state")[1]
    assert state["ai"]["configured"] is True and state["ai"]["hint"] == "...1234" and state["niche"] == "gta 6"


def test_state_includes_how_the_live_trends_are_doing(app, monkeypatch):
    trends = app.get("/api/state")[1]["trends"]
    assert trends["enabled"] is False and trends["updated"] is None and trends["due"] is True  # the test suite switches them off
    monkeypatch.setenv("CLIPPER_LIVE_TRENDS", "1")
    assert app.get("/api/state")[1]["trends"]["enabled"] is True


def test_a_full_run_from_a_pasted_link_to_finished_clips_with_files_on_the_desktop(app):
    status, data = app.post("/api/jobs", {"kind": "run", "url": VIDEO + "&list=PLx", "style": "drama", "top": 2, "niche": "reality tv"})
    assert status == 201
    snap = app.wait(data["id"])
    assert snap["status"] == "done" and snap["returncode"] == 0 and snap["error"] is None
    assert snap["progress"]["percent"] == 100 and all(s["state"] == "done" for s in snap["progress"]["steps"])
    assert any("Reading what's trending right now" in line for line in snap["lines"])  # live trends ran as part of the job
    sid = snap["session"]
    status, session = app.get(f"/api/sessions/{sid}")
    assert status == 200 and [p["rendered"] for p in session["picks"]] == [True, True, False, False]
    assert session["picks"][0]["social"]["trend_tags"] == ["drama"]
    exported = sorted(p.name for p in (app.home / "Desktop" / "clipper-exports" / sid).iterdir())
    assert exported == ["01 - he-said-i-never-loved-you-post.mp4", "01 - he-said-i-never-loved-you-post.txt",
                        "02 - the-walk-out-post.mp4", "02 - the-walk-out-post.txt", "captions.txt", "review.csv"]
    assert app.get("/api/sessions")[1]["sessions"][0]["id"] == sid


def test_choosing_the_moments_yourself_then_making_just_those(app):
    found = app.run_job({"kind": "find", "url": VIDEO, "style": "wild"})
    assert found["status"] == "done" and found["session"]
    sid = found["session"]
    assert app.get(f"/api/sessions/{sid}")[1]["picks"][0]["rendered"] is False
    made = app.run_job({"kind": "process", "session": sid, "picks": [2, 4], "export": False})
    assert made["status"] == "done" and made["session"] == sid
    rendered = [p["rendered"] for p in app.get(f"/api/sessions/{sid}")[1]["picks"]]
    assert rendered == [False, True, False, True]
    assert not (app.home / "Desktop" / "clipper-exports").exists()  # "also copy to Desktop" was off
    again = app.run_job({"kind": "export", "session": sid})
    assert again["status"] == "done" and again["progress"]["exported_to"].endswith(sid)


def test_a_video_with_no_captions_fails_with_a_plain_sentence_and_the_log(app):
    snap = app.run_job({"kind": "run", "url": "https://www.youtube.com/watch?v=FAILFAILFAI"})
    assert snap["status"] == "failed" and snap["returncode"] == 1
    assert "no captions on YouTube" in snap["error"]
    assert any("No subtitle available" in line for line in snap["lines"])


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"kind": "run", "url": "https://vimeo.com/1"}, "YouTube link"),
        ({"kind": "run"}, "Paste a YouTube link"),
        ({"kind": "run", "url": VIDEO, "top": 99}, "between 1 and 10"),
        ({"kind": "process", "session": "20261105_999999", "picks": [1]}, "can't find that session"),
        ({"kind": "nope"}, "don't know how"),
    ],
)
def test_bad_requests_get_a_400_with_a_sentence(app, body, fragment):
    status, data = app.post("/api/jobs", body)
    assert status == 400 and fragment in data["error"]


def test_only_one_job_runs_at_a_time_and_it_can_be_cancelled(app, monkeypatch):
    slow = {"kind": "run", "url": "https://www.youtube.com/watch?v=SLOWSLOWSLO"}
    status, first = app.post("/api/jobs", slow)
    assert status == 201
    deadline = time.time() + 20
    while app.get("/api/state")[1]["busy"] != first["id"] and time.time() < deadline:
        time.sleep(0.1)
    status, second = app.post("/api/jobs", {"kind": "run", "url": VIDEO})
    assert status == 409 and "still working" in second["error"]
    assert app.post(f"/api/jobs/{first['id']}/cancel")[1] == {"cancelled": True}
    snap = app.wait(first["id"], 30)
    assert snap["status"] == "cancelled" and snap["error"] is None
    assert app.post(f"/api/jobs/{first['id']}/cancel")[1] == {"cancelled": False}  # already over
    assert app.get("/api/state")[1]["busy"] is None


def test_polling_never_repeats_or_loses_a_log_line(app):
    status, data = app.post("/api/jobs", {"kind": "find", "url": VIDEO})
    seen, since = [], 0
    for _ in range(300):
        _, snap = app.get(f"/api/jobs/{data['id']}?since={since}")
        seen += snap["lines"]
        assert snap["next"] == since + len(snap["lines"])
        since = snap["next"]
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    _, full = app.get(f"/api/jobs/{data['id']}")
    assert seen == full["lines"] and len(seen) == since
    assert app.get(f"/api/jobs/{data['id']}?since=abc")[1]["lines"] == full["lines"]  # nonsense falls back to "from the start"
    assert app.get("/api/jobs/doesnotexist")[0] == 404


# --- playing a clip ---------------------------------------------------------------------------------------------


@pytest.fixture
def clip(app):
    snap = app.run_job({"kind": "run", "url": VIDEO, "top": 1})
    sid = snap["session"]
    path = app.server.out_dir / "sessions" / sid / "clips" / "clip_000" / "master.mp4"
    return sid, path.read_bytes()


def test_a_whole_clip_is_served_as_video_with_its_exact_size(app, clip):
    sid, data = clip
    status, headers, body = app.request("GET", f"/media/{sid}/0")
    assert status == 200 and body == data and headers["content-type"] == "video/mp4"
    assert headers["accept-ranges"] == "bytes" and int(headers["content-length"]) == len(data)


@pytest.mark.parametrize(
    "header, first, last",
    [("bytes=0-99", 0, 99), ("bytes=100-", 100, None), ("bytes=-50", None, None), ("bytes=10-5000000", 10, None)],
)
def test_a_range_request_gets_exactly_those_bytes_so_the_browser_can_seek(app, clip, header, first, last):
    sid, data = clip
    size = len(data)
    status, headers, body = app.request("GET", f"/media/{sid}/0", headers={"Range": header})
    start = size - 50 if header == "bytes=-50" else first
    end = size - 1 if last is None else last
    assert status == 206 and body == data[start : end + 1]
    assert headers["content-range"] == f"bytes {start}-{end}/{size}" and int(headers["content-length"]) == len(body)


def test_a_range_past_the_end_is_refused_and_junk_ranges_are_ignored(app, clip):
    sid, data = clip
    status, headers, _ = app.request("GET", f"/media/{sid}/0", headers={"Range": "bytes=99999999-"})
    assert status == 416 and headers["content-range"] == f"bytes */{len(data)}"
    for junk in ("bytes=abc", "items=0-5", "bytes=-", "bytes=0-5,10-15", "bytes=9-3"):
        status, _, body = app.request("GET", f"/media/{sid}/0", headers={"Range": junk})
        assert status == 200 and body == data, junk


def test_a_clip_that_does_not_exist_is_a_404(app, clip):
    sid, _ = clip
    assert app.request("GET", f"/media/{sid}/3")[0] == 404
    assert app.request("GET", "/media/20201231_235959/0")[0] == 404


def test_parse_range_unit():
    assert parse_range(None, 100) is None and parse_range("bytes=0-9", 0) is None
    assert parse_range("bytes=0-9", 100) == (0, 9) and parse_range("bytes=90-", 100) == (90, 99) and parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=-500", 100) == (0, 99) and parse_range("bytes=5-500", 100) == (5, 99)
    for bad in ("bytes=100-", "bytes=-0"):
        with pytest.raises(RangeNotSatisfiable):
            parse_range(bad, 100)


# --- the buttons that open folders, run checks ---------------------------------------------------------------------------


def test_open_only_opens_the_project_or_a_sessions_own_folders(app, monkeypatch):
    opened = []
    monkeypatch.setattr(export, "reveal", lambda path: opened.append(Path(path)) or True)
    snap = app.run_job({"kind": "run", "url": VIDEO, "top": 1})
    sid = snap["session"]
    assert app.post("/api/open", {"what": "project"})[1]["opened"] is True
    assert app.post("/api/open", {"what": "clips", "session": sid})[0] == 200
    assert app.post("/api/open", {"what": "export", "session": sid})[0] == 200
    assert opened == [app.server.project_dir, app.server.out_dir / "sessions" / sid / "clips", app.home / "Desktop" / "clipper-exports" / sid]
    for body in ({"what": "/etc"}, {"what": "../.."}, {"what": "clips", "session": "../.."}, {"what": "clips"}, {}):
        assert app.post("/api/open", body)[0] == 400, body
    assert len(opened) == 3  # none of those opened anything
    assert app.post("/api/open", {"what": "export", "session": "20201231_235959"})[0] == 400


def test_the_export_folder_is_not_opened_before_anything_was_exported(app, monkeypatch):
    monkeypatch.setattr(export, "reveal", lambda path: pytest.fail("nothing should open"))
    found = app.run_job({"kind": "find", "url": VIDEO})
    status, data = app.post("/api/open", {"what": "export", "session": found["session"]})
    assert status == 404 and "nothing there yet" in data["error"]


def test_the_setup_check_lists_each_finding_and_says_whether_all_is_well(app, monkeypatch):
    status, data = app.post("/api/doctor", {})
    assert status == 200 and data["ok"] is True
    assert {"level": "ok", "message": "AI key set"} in data["rows"]
    assert any(r["level"] == "warn" and "cookies file not found" in r["message"] for r in data["rows"])
    monkeypatch.delenv("CLIPPER_API_KEY")
    data = app.post("/api/doctor", {})[1]
    assert data["ok"] is False and any(r["level"] == "fail" and "no API key" in r["message"] for r in data["rows"])
    checked = app.post("/api/doctor", {"ai": True})[1]  # with the key gone the stand-in still lists the AI test row
    assert {"level": "ok", "message": "the AI answered"} in checked["rows"]


def test_checking_the_trend_sources_reports_each_one_and_updates_the_status(app, monkeypatch):
    monkeypatch.setenv("CLIPPER_NICHE", "reality tv")
    data = app.post("/api/trends/check", {})[1]
    assert data["ok"] is True
    text = "\n".join(data["lines"])
    assert "Google Trends: 2 names" in text and "Using live trends from public feeds" in text
    assert data["status"]["sources"] >= 5 and "loveisblind" in data["status"]["tags"] and data["status"]["due"] is False
    assert any("Love Is Blind" in hot for hot in data["status"]["hot"])


def test_the_trend_check_says_so_when_nothing_can_be_read(app, monkeypatch):
    monkeypatch.setenv("FAKE_TRENDS", "offline")
    data = app.post("/api/trends/check", {})[1]
    assert data["ok"] is False and any("FAILED" in line for line in data["lines"]) and "Nothing could be read" in "\n".join(data["lines"])


def test_the_background_refresher_reads_only_when_the_copy_is_out_of_date_and_stops_with_the_server(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.server.jobs, "run_quick", lambda args, timeout=90: calls.append(tuple(args)) or (0, ""))
    states = iter([
        {"enabled": True, "due": True, "failure": None},    # out of date: read
        {"enabled": True, "due": False, "failure": None},   # fresh: leave alone
        {"enabled": False, "due": True, "failure": None},   # switched off: leave alone
        {"enabled": True, "due": True, "failure": "offline"},  # failed recently: wait
    ] + [{"enabled": True, "due": False, "failure": None}] * 50)
    monkeypatch.setattr(srv, "trend_status", lambda env: next(states))
    thread = app.server.start_trend_refresher(first_after=0.01, every=0.02)
    deadline = time.time() + 5
    while len(calls) < 1 and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)  # long enough for the other three states to have been seen
    assert calls == [("trends", "--live")]
    app.server._stop.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


# --- starting it up -----------------------------------------------------------------------------------------------------------


def test_it_moves_to_the_next_port_when_the_first_is_taken_and_opens_the_browser(tmp_path, monkeypatch, capsys):
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    opened = []
    monkeypatch.setattr(srv.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(ClipperServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(ClipperServer, "start_trend_refresher", lambda self, *a, **k: None)
    try:
        assert srv.serve(tmp_path, "out", port=taken) == 0
    finally:
        blocker.close()
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:") and "?token=" in opened[0]
    port = int(opened[0].split(":")[2].split("/")[0])
    assert taken < port <= taken + srv.PORT_TRIES
    out = capsys.readouterr().out
    assert "CLIPPER is running" in out and "token=" not in out  # the secret is not printed when the browser opened


def test_if_the_browser_cannot_be_opened_the_address_is_printed_instead(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(srv.webbrowser, "open", lambda url: False)
    monkeypatch.setattr(ClipperServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(ClipperServer, "start_trend_refresher", lambda self, *a, **k: None)
    assert srv.serve(tmp_path, "out", port=0) == 0
    assert "Open this in your browser: http://127.0.0.1:" in capsys.readouterr().out
    assert srv.serve(tmp_path, "out", port=0, open_browser=False) == 0
