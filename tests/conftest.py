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


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch, tmp_path_factory):
    """No test may read a real .env, use a real API key (which would spend real money), touch the
    real trends cache, or run web research unless it asks for it."""
    monkeypatch.setenv("CLIPPER_SKIP_DOTENV", "1")
    for var in (
        "CLIPPER_API_KEY", "DASHSCOPE_API_KEY", "CLIPPER_BASE_URL", "CLIPPER_MODEL",
        "CLIPPER_NICHE", "CLIPPER_TRENDS", "CLIPPER_REGION", "CLIPPER_SEARCH_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLIPPER_AUTO_TRENDS", "0")  # tests of automatic research switch it on
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
