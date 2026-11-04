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
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "test-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.server.reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence request logging
        pass


@pytest.fixture
def fake_llm(monkeypatch):
    """A local OpenAI-compatible server. Set ``.reply``; inspect ``.requests``."""
    # The sandbox sets proxy variables; localhost must bypass them.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.reply = "[]"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base_url = f"http://127.0.0.1:{server.server_port}/compatible-mode/v1"
    yield server
    server.shutdown()
    server.server_close()


def highlight(start, end, title="T", score=8, hook="H"):
    return {
        "start_time": start,
        "end_time": end,
        "title": title,
        "description": "d",
        "virality_score": score,
        "hook_text": hook,
    }
