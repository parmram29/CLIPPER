import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint that records requests."""

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        if self.server.reject_response_format and "response_format" in body:
            error = json.dumps({"error": {"message": "response_format is not supported"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error)))
            self.end_headers()
            self.wfile.write(error)
            return
        native = self.path.endswith("/generation")  # Alibaba's native API, not the compatible one
        options = (body.get("parameters") or {}).get("search_options") or {}
        if self.server.reject_strategy and "search_strategy" in options:
            refused_strategy = True
        else:
            refused_strategy = False
        refused = self.server.reject_keys & (set(body) | set(body.get("parameters") or {}))
        if refused_strategy:
            refused = {"search_strategy"}
        if refused:
            error = json.dumps({"error": {"message": f"unsupported parameter(s): {sorted(refused)}"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error)))
            self.end_headers()
            self.wfile.write(error)
            return
        reply = self.server.reply_fn(body) if self.server.reply_fn else self.server.reply
        if native:
            output = {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}]}
            if "search_info" in self.server.extra_response:
                output["search_info"] = self.server.extra_response["search_info"]
            data = json.dumps({"request_id": "r", "output": output, "usage": {"input_tokens": 11, "output_tokens": 22}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "test-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        }
        payload.update(self.server.extra_response)  # e.g. a provider's `search_info`
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence request logging
        pass


class _ClaudeHandler(BaseHTTPRequestHandler):
    """Anthropic's ``/v1/messages`` endpoint as the official SDK uses it (a server-sent event stream),
    recording every request. Errors are answered in Anthropic's own error format."""

    @staticmethod
    def _event(name, payload):
        return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        server = self.server
        server.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

        failure = server.errors.pop(0) if server.errors else server.always_fail
        if failure:
            status, kind, message, extra = failure
            data = json.dumps({"type": "error", "error": {"type": kind, "message": message, **extra}, "request_id": "req_test"}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        text = server.reply_fn(body) if server.reply_fn else server.reply
        model = body.get("model", "claude-test")
        usage = server.usage
        message = {
            "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": 1},
        }
        out = [self._event("message_start", {"type": "message_start", "message": message}), self._event("ping", {"type": "ping"})]
        index = 0
        if server.thinking:  # the newest models always think first; the answer is the text block after it
            out.append(self._event("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            out.append(self._event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": "Let me weigh the moments. [{not the answer}]"}}))
            out.append(self._event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": "sig"}}))
            out.append(self._event("content_block_stop", {"type": "content_block_stop", "index": index}))
            index += 1
        if text:
            out.append(self._event("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
            third = max(1, len(text) // 3)  # in pieces, like a real stream
            for start in range(0, len(text), third):
                out.append(self._event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text[start : start + third]}}))
            out.append(self._event("content_block_stop", {"type": "content_block_stop", "index": index}))
        delta = {"stop_reason": server.stop_reason, "stop_sequence": None}
        if server.stop_details:
            delta["stop_details"] = server.stop_details
        out.append(self._event("message_delta", {"type": "message_delta", "delta": delta, "usage": {"output_tokens": usage[1]}}))
        out.append(self._event("message_stop", {"type": "message_stop"}))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b"".join(out))
        self.wfile.flush()

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch, tmp_path_factory):
    """No test may read a real .env, use a real API key (which would spend real money), touch the
    real trends cache, or run web research unless it asks for it."""
    monkeypatch.setenv("CLIPPER_SKIP_DOTENV", "1")
    for var in (
        "CLIPPER_API_KEY", "DASHSCOPE_API_KEY", "CLIPPER_BASE_URL", "CLIPPER_MODEL",
        "CLIPPER_NICHE", "CLIPPER_TRENDS", "CLIPPER_REGION", "CLIPPER_SEARCH_MODEL",
        "CLIPPER_COOKIES",  # the CLI sets this for the downloader; it must not leak from one test into the next
        "CLIPPER_TREND_SOURCES", "CLIPPER_DRAMA_SHARE",
        # Claude settings, and the Anthropic variables the SDK would otherwise pick up by itself
        "CLIPPER_PROVIDER", "CLIPPER_EFFORT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLIPPER_AUTO_TRENDS", "0")  # tests of automatic research switch it on
    monkeypatch.setenv("CLIPPER_LIVE_TRENDS", "0")  # tests of the live feeds switch it on and fake the network
    monkeypatch.setenv("CLIPPER_CACHE_DIR", str(tmp_path_factory.mktemp("clipper-cache")))


@pytest.fixture
def fake_llm(monkeypatch):
    """A local OpenAI-compatible server.

    Set ``.reply`` for a fixed answer, or ``.reply_fn(request_body)`` to answer per request;
    set ``.reject_response_format`` to make it refuse JSON mode like some providers do.
    Inspect ``.requests``.
    """
    # The sandbox sets proxy variables; localhost must bypass them.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.reply = "[]"
    server.reply_fn = None
    server.reject_response_format = False
    server.reject_keys = set()  # request fields to refuse with a 400, e.g. {"enable_search"}
    server.reject_strategy = False  # refuse search_options.search_strategy (a nested field)
    server.extra_response = {}  # extra top-level fields to add to every reply
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base_url = f"http://127.0.0.1:{server.server_port}/compatible-mode/v1"
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def fake_claude(monkeypatch):
    """A local Anthropic API, spoken to by the real ``anthropic`` SDK.

    Set ``.reply`` for a fixed answer, or ``.reply_fn(request_body)`` to answer per request; ``.thinking``
    puts a thinking block before the answer (as the newest models do); ``.stop_reason`` / ``.stop_details``
    end the reply early (a refusal, running out of room); ``.usage`` is ``(input, output)`` tokens.
    ``.fail(status, kind, message, **extra)`` answers the next request with an Anthropic error;
    ``.always_fail`` answers every request that way. Inspect ``.requests``; ``.base_url`` is where to point CLIPPER.
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    from clipper import claude_client

    monkeypatch.setattr(claude_client, "RETRIES", 0)  # the SDK retries 429/5xx with pauses; tests read the error straight away

    server = HTTPServer(("127.0.0.1", 0), _ClaudeHandler)
    server.requests = []
    server.reply = "[]"
    server.reply_fn = None
    server.thinking = False
    server.stop_reason = "end_turn"
    server.stop_details = None
    server.usage = (11, 22)
    server.errors = []
    server.always_fail = None
    server.fail = lambda status, kind, message, **extra: server.errors.append((status, kind, message, extra))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    yield server
    server.shutdown()
    server.server_close()


def clock_text(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def make_transcript(minutes, step=5):
    """A transcript in upstream's format: one line every ``step`` seconds, 'words at <second>'."""
    return "\n".join(
        f"[{clock_text(t)},000 - {clock_text(t + step - 1)},000] words at {t}"
        for t in range(0, int(minutes * 60), step)
    )


def write_srt(path, seconds, step=5):
    cues = []
    for i, t in enumerate(range(0, int(seconds), step), 1):
        cues.append(f"{i}\n{clock_text(t)},000 --> {clock_text(t + step - 1)},000\nwords at {t}\n")
    path.write_text("\n".join(cues), encoding="utf-8")


def highlight(start, end, title="T", score=8, hook="H"):
    return {
        "start_time": start,
        "end_time": end,
        "title": title,
        "description": "d",
        "virality_score": score,
        "hook_text": hook,
    }


@pytest.fixture(scope="module")
def gui_real_run(tmp_path_factory):
    """One complete `clipper run` through the stand-in pipeline (tests/gui_fake_cli.py): the real command
    line and export code with only the internet-bound parts faked. Gives (project folder, home, its log lines)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    base = tmp_path_factory.mktemp("gui-real-run")
    (base / "home").mkdir()
    env = {
        **os.environ, "HOME": str(base / "home"), "CLIPPER_API_KEY": "test-key", "CLIPPER_CACHE_DIR": str(base / "cache"),
        "FAKE_DELAY": "0", "CLIPPER_SKIP_DOTENV": "1", "PYTHONUNBUFFERED": "1",
    }
    stub = Path(__file__).parent / "gui_fake_cli.py"
    done = subprocess.run(
        [sys.executable, "-u", str(stub), "run", "https://www.youtube.com/watch?v=abcdefghijk", "--clips=8", "--profile=viral",
         f"--out={base / 'out'}", "--top=2", "--export", "--niche=reality tv"],
        cwd=base, env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return base, base / "home", done.stdout.splitlines()
