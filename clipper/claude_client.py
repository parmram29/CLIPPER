"""Claude, through Anthropic's official ``anthropic`` Python SDK.

Not part of upstream. This is the only module that imports ``anthropic``: ``llm.chat`` and the
highlight finder hand it the requests that are meant for Claude, and keep their OpenAI-compatible
route for any other provider. What is different about Claude, and handled here:

* No ``temperature``, ``top_p`` or ``top_k``. Current Claude models answer a request that carries any
  of them with HTTP 400, so none is ever sent (the SDK's ``create()`` does not even take them).
* The newest models always think before they answer, and that thinking counts against
  ``max_tokens``. A caller's "I expect about N tokens back" is topped up with room to think, so a
  short answer is not cut off by its own reasoning. The figure is a ceiling, not a target: only the
  tokens actually produced are billed.
* A reply is a list of blocks (thinking, text, ...). Only the ``text`` blocks are the answer.
* A request can end in a refusal or by running out of room. Both are reported as errors with a plain
  explanation instead of coming back as an empty or half-finished answer.
* Requests are streamed, which is what Anthropic recommends for anything that may think for a
  while: a long non-streaming request can look like a dead connection.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BASE_URL = "https://api.anthropic.com"
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # how hard Claude thinks; the models' own default is medium

THINKING_ROOM = 12_000  # tokens of headroom for thinking, added to the answer length the caller expects
DEFAULT_ANSWER_TOKENS = 8_000  # when the caller gives no figure (the list of highlights)
MAX_OUTPUT_TOKENS = 32_000  # every current Claude model allows at least 64k; streaming makes this safe
RETRIES = 3  # the SDK retries connection errors, 408, 409, 429 and 5xx with growing pauses
READ_TIMEOUT = 600.0  # seconds without a byte from Anthropic: the SDK's own default. Long thinking must not be mistaken for a dead connection (a retry would be billed again)


class ClaudeError(RuntimeError):
    """A request to Claude failed. The message is written for the person running CLIPPER."""


def _sdk():
    """The ``anthropic`` module, or a ``ClaudeError`` that says how to get it."""
    try:
        import anthropic
    except ImportError as exc:  # depends on the person's install
        raise ClaudeError(
            "The `anthropic` package is not installed. Double-click CLIPPER.command again (or run ./setup.sh) "
            "so it installs everything CLIPPER needs."
        ) from exc
    return anthropic


def max_tokens_for(answer_tokens: int | None) -> int:
    """The ``max_tokens`` to send for an answer of about ``answer_tokens``, with room to think first."""
    answer = answer_tokens if answer_tokens and answer_tokens > 0 else DEFAULT_ANSWER_TOKENS
    return min(answer + THINKING_ROOM, MAX_OUTPUT_TOKENS)


def _from_body(exc: Any, *path: str) -> Any:
    """A field of the error body Anthropic sent (``error.type``, ``error.message``, ...), whichever SDK release this is.
    Newer releases also expose these as attributes; the body is in every release."""
    node = getattr(exc, "body", None)
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _detail(exc: Any) -> str:
    """The reason Anthropic gave, without the SDK's own prefix ("Error code: 400 - {...}"), kept short."""
    text = getattr(exc, "api_message", None) or _from_body(exc, "error", "message") or getattr(exc, "message", None) or str(exc)
    return " ".join(str(text).split())[:300]


def explain(exc: Exception, model: str = "") -> str:
    """A plain-English reason for a failed request, with what to do about it.

    Kept short, with the point first: the per-window log line cuts a message at 160 characters."""
    anthropic = _sdk()
    detail = _detail(exc)
    lowered = detail.lower()
    status = getattr(exc, "status_code", None)
    kind = getattr(exc, "type", None) or _from_body(exc, "error", "type")
    code = getattr(exc, "error_code", None) or _from_body(exc, "error", "details", "error_code")

    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Anthropic rejected the API key (401). CLIPPER_API_KEY in .env must be a Claude key (starts with sk-ant-): "
            "platform.claude.com/settings/keys"
        )
    if status == 402 or kind == "billing_error" or "credit balance" in lowered:
        return (
            "Your Anthropic account is out of credit. Add some at platform.claude.com/settings/billing "
            "(a Claude.ai subscription does not pay for the API)."
        )
    if isinstance(exc, anthropic.RateLimitError):
        if code == "enforced_spend_limit_reached" or "spend" in lowered:
            return (
                "Your Anthropic account has reached its monthly spend limit. Raise it at platform.claude.com "
                "or wait for it to reset."
            )
        return "Anthropic says you are sending requests too fast (429). Wait a minute and run it again."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"Anthropic says this key is not allowed to do that (403): {detail}"
    if isinstance(exc, anthropic.NotFoundError):
        return (
            f"Anthropic does not know the model '{model}' (404). In .env set CLIPPER_MODEL={DEFAULT_MODEL}, "
            "claude-sonnet-5-5 or claude-haiku-5-5, or delete that line."
        )
    if isinstance(exc, anthropic.BadRequestError):
        return f"Anthropic refused the request (400): {detail}"
    if isinstance(exc, anthropic.APITimeoutError):
        return "Anthropic took too long to answer. Run it again; if it keeps happening, use a shorter video."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach Anthropic (api.anthropic.com). Check the internet connection and any VPN, then run it again."
    if isinstance(exc, anthropic.APIStatusError) and (status or 0) >= 500:
        return (
            f"Anthropic's service is busy or down right now (HTTP {status}). Run it again in a minute "
            "(status.anthropic.com shows outages)."
        )
    return f"Anthropic API error: {detail}"


def reply_text(message: Any) -> str:
    """The answer in a finished reply: its text blocks joined, or a ``ClaudeError`` saying why there is none."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        why = getattr(details, "explanation", None) or getattr(details, "category", None)
        raise ClaudeError(
            "Claude declined to answer this request" + (f" ({why})" if why else "") + ". "
            "Run it again, or use --direction to steer it toward a different kind of moment."
        )
    text = "".join(
        getattr(block, "text", "") or "" for block in (getattr(message, "content", None) or []) if getattr(block, "type", None) == "text"
    ).strip()
    if stop == "max_tokens":
        raise ClaudeError(
            "Claude ran out of room before it finished its reply (it uses part of its allowance to think first). "
            "Run it again, or add CLIPPER_EFFORT=low to the .env file, or use a shorter video."
        )
    if not text:
        raise ClaudeError("Claude sent back no text. Run it again.")
    return text


def json_array_part(text: str) -> str:
    """The JSON array of objects inside a reply that has a sentence before it, code fences around it, or a
    remark after it. Claude does this now and then even when told to reply with JSON only.

    Cautious on purpose: if no complete array of objects can be found (the reply was cut off, or is broken),
    the text is returned unchanged, so the caller's own repair of broken JSON still sees all of it.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\[", text):
        try:
            value, end = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return text[match.start() : end]
    return text


def token_usage(message: Any) -> dict[str, int]:
    """Token counts in the shape the rest of CLIPPER uses (``prompt_tokens`` / ``completion_tokens``)."""
    usage = getattr(message, "usage", None)

    def count(name: str) -> int:
        value = getattr(usage, name, 0) if usage else 0
        return value if isinstance(value, int) else 0

    prompt = count("input_tokens") + count("cache_creation_input_tokens") + count("cache_read_input_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": count("output_tokens")}


def client(api_key: str, base_url: str | None = None):
    """An SDK client for this key. The key and address are always passed in, so nothing in the
    environment (``ANTHROPIC_AUTH_TOKEN``, ``ANTHROPIC_BASE_URL``, a saved profile) can change where
    the key goes or which credential is used."""
    anthropic = _sdk()
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"),
        timeout=anthropic.Timeout(READ_TIMEOUT, connect=20.0),
        max_retries=RETRIES,
    )


def message(
    *,
    api_key: str,
    model: str,
    user: str,
    system: str | None = None,
    max_tokens: int | None = None,
    base_url: str | None = None,
    effort: str | None = None,
    json_only: bool = False,
) -> tuple[str, dict[str, int]]:
    """One request to Claude. Returns ``(text, usage)``; raises ``ClaudeError`` with a plain message.

    ``max_tokens`` is the length of answer expected; room to think is added (see ``max_tokens_for``).
    ``effort`` is how hard Claude thinks (``low`` to ``max``); left unset it uses the model's own default.
    ``json_only`` asks for nothing but JSON, since Claude has no JSON switch the way some providers do.
    """
    anthropic = _sdk()
    if effort is not None and effort not in EFFORTS:
        raise ClaudeError(f"effort must be one of {', '.join(EFFORTS)}, not {effort!r}")
    if json_only:
        system = ((system + "\n\n") if system else "") + "Reply with the JSON only: no text before it, no text after it."

    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens_for(max_tokens),
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        request["system"] = system
    if effort:
        request["output_config"] = {"effort": effort}

    try:
        with client(api_key, base_url).messages.stream(**request) as stream:
            final = stream.get_final_message()
    except anthropic.APIError as exc:  # every error the SDK raises: status, connection, timeout
        raise ClaudeError(explain(exc, model)) from exc
    return reply_text(final), token_usage(final)


def from_ai(ai: Mapping[str, Any], user: str, *, system: str | None = None, max_tokens: int | None = None, json_only: bool = False):
    """``message()`` for an ``ai`` settings dict (``api_key``, ``base_url``, ``model``, optional ``effort``)."""
    return message(
        api_key=ai["api_key"],
        model=ai["model"],
        base_url=ai.get("base_url"),
        effort=ai.get("effort"),
        user=user,
        system=system,
        max_tokens=max_tokens,
        json_only=json_only,
    )
