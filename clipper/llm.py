"""A small helper for chat calls to any OpenAI-compatible endpoint.

Not part of upstream. Used for the cross-window re-scoring and for post copy. Upstream's
highlight finder keeps its own client.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0}


def chat(
    ai: Mapping[str, Any],
    user: str,
    *,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    json_mode: bool = False,
    timeout: float = 120.0,
) -> tuple[str, dict[str, int]]:
    """One chat completion. Returns ``(text, usage)``.

    With ``json_mode`` it asks for a JSON object, and retries without that option when the
    provider rejects it (not every OpenAI-compatible endpoint supports ``response_format``).
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=ai["api_key"],
        base_url=ai.get("base_url", "https://api.openai.com/v1"),
        timeout=timeout,
        max_retries=3,
    )
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    kwargs: dict[str, Any] = {"model": ai["model"], "messages": messages, "temperature": temperature}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    if json_mode:
        try:
            response = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
        except Exception:  # noqa: BLE001 - provider without response_format support
            response = client.chat.completions.create(**kwargs)
    else:
        response = client.chat.completions.create(**kwargs)

    if not response or not getattr(response, "choices", None):
        raise RuntimeError("the AI response had no choices; check the provider, key and model name")
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("the AI returned an empty reply (refusal, or quota exceeded)")

    usage = getattr(response, "usage", None)
    return content, {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    }


class SearchUnavailable(RuntimeError):
    """The endpoint refused a web-search request (wrong model, no access, or no such feature)."""


# Alibaba's documented switch is `enable_search`; the extra options below are tried first and
# dropped if the endpoint rejects them. The OpenAI SDK has no field for any of this, so it
# goes in extra_body, which is merged into the request.
_COMPAT_ATTEMPTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("enable_search + options", {"enable_search": True, "search_options": {"forced_search": True, "enable_source": True}}),
    ("enable_search only", {"enable_search": True}),
)


def extract_search_sources(node: Any) -> list[dict[str, Any]]:
    """Find the web pages a search-enabled reply says it used, wherever the provider put them.

    Alibaba documents these as ``search_info.search_results``; rather than trust one exact
    location, walk the whole response and collect any ``search_results`` list.
    """
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "search_results" and isinstance(inner, list):
                    found.extend(item for item in inner if isinstance(item, dict))
                else:
                    walk(inner)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return found


NATIVE_PATH = "/api/v1/services/aigc/text-generation/generation"

# Forms of the native request, most capable first, each tried only if the one before is refused.
# Alibaba documents the "agent" strategy as multi-round retrieval: it can search more than once,
# which a single quick lookup (what a long prompt tends to trigger) does not.
_NATIVE_ATTEMPTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "agent search",
        {"enable_search": True, "search_options": {"forced_search": True, "enable_source": True, "search_strategy": "agent"}},
    ),
    ("enable_search + options", {"enable_search": True, "search_options": {"forced_search": True, "enable_source": True}}),
    ("enable_search only", {"enable_search": True}),
)


def search_forms(api: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The request forms for a route, most capable first (used to test each one on its own)."""
    return _NATIVE_ATTEMPTS if api == "dashscope" else _COMPAT_ATTEMPTS


def native_search_url(base_url: str) -> str:
    """Alibaba's native text-generation endpoint on the same host as the compatible-mode URL."""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # urllib would also open file:// and ftp:// addresses, so only web addresses get through
        raise SearchUnavailable(f"CLIPPER_BASE_URL must be an http:// or https:// address, got {base_url!r}")
    return f"{parsed.scheme}://{parsed.netloc}{NATIVE_PATH}"


def chat_with_search_native(
    ai: Mapping[str, Any],
    user: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    timeout: float = 180.0,
    info: dict[str, Any] | None = None,
    attempts: Sequence[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    """The same search call through Alibaba's NATIVE API, which (unlike the OpenAI-compatible
    endpoint) can return the sources it used, as ``output.search_info.search_results``.

    Request and reply shapes follow Alibaba's published description of this API. Like the
    compatible-mode call it tries the options form first and the plain switch second.
    """
    import urllib.error
    import urllib.request

    url = native_search_url(str(ai.get("base_url", "")))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    data: dict[str, Any] | None = None
    last_error: Exception | None = None
    refused: list[str] = []
    if info is not None:
        info["refused"] = refused
    for label, extra in (attempts if attempts is not None else _NATIVE_ATTEMPTS):
        body = {
            "model": ai["model"],
            "input": {"messages": messages},
            "parameters": {
                "result_format": "message",
                "temperature": temperature,
                "max_tokens": max_tokens,
                **extra,
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {ai['api_key']}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 (scheme checked in native_search_url)
                data = json.loads(response.read().decode("utf-8"))
            if info is not None:
                info["request"] = f"native API: {label}"
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:700]
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            refused.append(f"{label}: {str(last_error)[:300]}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
            refused.append(f"{label}: {type(exc).__name__}: {str(exc)[:300]}")
    if data is None:
        raise SearchUnavailable(f"native web search request failed: {str(last_error)[:700]}") from last_error

    output = data.get("output") or {}
    choices = output.get("choices") or []
    content = ""
    if choices:
        content = str((choices[0].get("message") or {}).get("content") or "").strip()
    content = content or str(output.get("text") or "").strip()
    if not content:
        raise SearchUnavailable("the native search reply was empty")
    usage = data.get("usage") or {}
    return (
        content,
        {
            "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
            "completion_tokens": int(usage.get("output_tokens", 0) or 0),
        },
        extract_search_sources(output),
    )


def chat_with_search(
    ai: Mapping[str, Any],
    user: str,
    *,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    timeout: float = 180.0,
    info: dict[str, Any] | None = None,
    api: str = "compat",
    attempts: Sequence[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[str, dict[str, int], list[dict[str, Any]]]:
    """A chat call with the provider's web search switched on.

    Returns ``(text, usage, sources)``. ``sources`` is empty when the provider did not say
    which pages it used, which the caller must treat as "cannot verify", not as "none".
    Raises SearchUnavailable if the endpoint rejects every form of the request.

    ``api="compat"`` uses the OpenAI-compatible endpoint, which per Alibaba's docs never
    returns sources. ``api="dashscope"`` uses Alibaba's native API, which can.

    If ``info`` is given it is filled with which form of the request was accepted
    (``info["request"]``) and why any earlier form was refused (``info["refused"]``), for
    diagnostics. ``attempts`` restricts the forms tried, for testing one on its own.
    """
    if api == "dashscope":
        return chat_with_search_native(
            ai, user, system=system, temperature=temperature, max_tokens=max_tokens, timeout=timeout,
            info=info, attempts=attempts,
        )
    from openai import OpenAI

    client = OpenAI(
        api_key=ai["api_key"],
        base_url=ai.get("base_url", "https://api.openai.com/v1"),
        timeout=timeout,
        max_retries=2,
    )
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    base: dict[str, Any] = {
        "model": ai["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    response = None
    last_error: Exception | None = None
    refused: list[str] = []
    if info is not None:
        info["refused"] = refused
    for label, extra in (attempts if attempts is not None else _COMPAT_ATTEMPTS):
        try:
            response = client.chat.completions.create(**base, extra_body=extra)
            if info is not None:
                info["request"] = label
            break
        except Exception as exc:  # noqa: BLE001 - try the plainer request before giving up
            last_error = exc
            refused.append(f"{label}: {str(exc)[:300]}")
    if response is None:
        raise SearchUnavailable(f"web search request failed: {str(last_error)[:700]}") from last_error

    if not getattr(response, "choices", None):
        raise SearchUnavailable("the search reply had no choices")
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise SearchUnavailable("the search reply was empty")

    usage = getattr(response, "usage", None)
    usage_dict = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    }
    return content, usage_dict, extract_search_sources(response.model_dump())


def extract_json(text: str) -> Any:
    """Parse the first JSON value in a model reply, tolerating fences and surrounding prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned, match.start())
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON found in the model's reply")


def add_usage(total: dict[str, int], more: Mapping[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens"):
        total[key] = total.get(key, 0) + int(more.get(key, 0) or 0)
