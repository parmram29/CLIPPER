"""A small helper for chat calls to any OpenAI-compatible endpoint.

Not part of upstream. Used for the cross-window re-scoring and for post copy. Upstream's
highlight finder keeps its own client.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

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
