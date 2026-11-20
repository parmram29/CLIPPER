"""Runtime configuration, read from the environment (optionally a ``.env`` file).

Not part of the upstream yt-short-clipper code; added for the headless CLI.

CLIPPER talks to Claude (Anthropic's API) unless it is pointed somewhere else. Put an Anthropic key in
``CLIPPER_API_KEY`` and nothing else is needed. Any OpenAI-compatible service still works: set
``CLIPPER_BASE_URL`` (and ``CLIPPER_MODEL``) to its address and model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .claude_client import DEFAULT_BASE_URL as ANTHROPIC_URL
from .claude_client import DEFAULT_MODEL, EFFORTS

ANTHROPIC = "anthropic"  # Claude, through Anthropic's own SDK (see claude_client.py)
OPENAI = "openai"  # any OpenAI-compatible service, through the openai SDK
ANTHROPIC_KEY_PREFIX = "sk-ant-"

# For an Alibaba Model Studio address (Singapore / international, or Beijing), a key from DASHSCOPE_API_KEY and
# this model are used when CLIPPER_API_KEY / CLIPPER_MODEL are not set. Keys are region-specific.
ALIBABA_DEFAULT_MODEL = "qwen-plus"

_PROVIDER_NAMES = {
    "anthropic": ANTHROPIC,
    "claude": ANTHROPIC,
    "openai": OPENAI,
    "openai-compatible": OPENAI,
    "compatible": OPENAI,
}


class ConfigError(Exception):
    """Raised when required configuration is missing or unusable."""


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    base_url: str
    model: str
    provider: str = OPENAI
    effort: str | None = None  # Claude only: low | medium | high | xhigh | max; None = the model's own default
    notes: tuple[str, ...] = ()  # settings that were ignored, and why (shown to the person, never an error)

    def as_dict(self) -> dict[str, str]:
        """The ``ai`` dict shape the pipeline functions expect."""
        ai = {"api_key": self.api_key, "base_url": self.base_url, "model": self.model, "provider": self.provider}
        if self.effort:
            ai["effort"] = self.effort
        return ai

    @property
    def masked_key(self) -> str:
        return "..." + self.api_key[-4:] if len(self.api_key) > 8 else "***"

    @property
    def is_claude(self) -> bool:
        return self.provider == ANTHROPIC

    @property
    def service(self) -> str:
        """Who answers, in words for the app: Claude, or the address of the other service."""
        return "Claude (Anthropic)" if self.is_claude else (_host(self.base_url) or self.base_url)

    def describe(self) -> str:
        """One line for the setup check. Never contains the key itself."""
        if self.is_claude:
            where = "" if self.base_url == ANTHROPIC_URL else f" @ {self.base_url}"
            effort = f", effort {self.effort}" if self.effort else ""
            return f"Claude (Anthropic): model {self.model}{where}{effort} (key {self.masked_key})"
        return f"model {self.model} @ {self.base_url} (key {self.masked_key})"


def load_dotenv_if_present(path: str | Path | None = None) -> bool:
    """Load ``.env`` from the current directory (or ``path``) without overriding
    variables that are already set. Returns True if a file was read."""
    if path is None and os.environ.get("CLIPPER_SKIP_DOTENV"):
        return False  # set by the test suite so a developer's real .env can never leak in
    env_path = Path(path) if path else Path.cwd() / ".env"
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(env_path, override=False)
    return True


def _clean(value: object) -> str:
    """A setting without the spaces, newline or quote marks that tend to come along when it is pasted."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_dashscope(base_url: str) -> bool:
    return _host(base_url).endswith("aliyuncs.com")


def is_anthropic_host(base_url: str) -> bool:
    host = _host(base_url)
    return host == "anthropic.com" or host.endswith(".anthropic.com")


def looks_like_anthropic_key(api_key: str) -> bool:
    return _clean(api_key).startswith(ANTHROPIC_KEY_PREFIX)


def provider_for(api_key: str, base_url: str | None = None) -> str:
    """Which provider a bare key and address belong to, for settings that do not say (an older ``ai`` dict).

    A Claude key, or Anthropic's own address, means Claude; anything else is an OpenAI-compatible service.
    """
    if looks_like_anthropic_key(api_key) or (base_url and is_anthropic_host(base_url)):
        return ANTHROPIC
    return OPENAI


def _anthropic_root(url: str) -> str:
    """Anthropic's address as the SDK wants it: no trailing slash and no ``/v1`` (the SDK adds that itself)."""
    root = url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def _choose_provider(clipper_key: str, anthropic_key: str, base_url: str) -> str:
    """The provider when ``CLIPPER_PROVIDER`` does not say: the key decides first, then the address."""
    if looks_like_anthropic_key(clipper_key):
        return ANTHROPIC
    elsewhere = bool(base_url) and not is_anthropic_host(base_url)
    if clipper_key:  # some other service's key
        return OPENAI if elsewhere else ANTHROPIC
    if looks_like_anthropic_key(anthropic_key):
        return ANTHROPIC
    return OPENAI if elsewhere else ANTHROPIC


def load_ai_config(env: Mapping[str, str] | None = None) -> AIConfig:
    """Build the AI config from ``CLIPPER_*`` variables.

    The provider is ``CLIPPER_PROVIDER`` (``anthropic`` or ``openai``) if set. Otherwise the key decides:
    one that starts with ``sk-ant-`` is a Claude key. Failing that, a ``CLIPPER_BASE_URL`` that is not
    Anthropic's means an OpenAI-compatible service, and with neither the answer is Claude.

    A Claude key is only ever sent to Anthropic. If ``.env`` still holds an old address or model from another
    service, they are ignored (and said so in ``notes``) rather than followed; set ``CLIPPER_PROVIDER=anthropic``
    to point Claude at a gateway of your own. Likewise ``ANTHROPIC_API_KEY`` is accepted as the Claude key,
    ``DASHSCOPE_API_KEY`` as the key for an Alibaba address, and no other provider's key (``OPENAI_API_KEY``
    and the like) is ever picked up, so it cannot be sent to the wrong host.
    """
    env = os.environ if env is None else env
    notes: list[str] = []

    clipper_key = _clean(env.get("CLIPPER_API_KEY"))
    anthropic_key = _clean(env.get("ANTHROPIC_API_KEY"))
    configured_url = _clean(env.get("CLIPPER_BASE_URL"))
    configured_model = _clean(env.get("CLIPPER_MODEL"))

    if configured_url:
        parsed = urlparse(configured_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(f"CLIPPER_BASE_URL is not a valid http(s) URL: {configured_url!r}")

    named = _clean(env.get("CLIPPER_PROVIDER")).lower()
    explicit = _PROVIDER_NAMES.get(named) if named else None
    if named and explicit is None:
        raise ConfigError(f"CLIPPER_PROVIDER must be 'anthropic' or 'openai', not {named!r}")
    provider = explicit or _choose_provider(clipper_key, anthropic_key, configured_url)

    effort = _clean(env.get("CLIPPER_EFFORT")).lower() or None
    if effort and effort not in EFFORTS:
        raise ConfigError(f"CLIPPER_EFFORT must be one of {', '.join(EFFORTS)}, not {effort!r}")

    if provider == ANTHROPIC:
        api_key = clipper_key or anthropic_key
        if not api_key:
            raise ConfigError(
                "No API key configured. Set CLIPPER_API_KEY in .env to your Anthropic key (it starts with sk-ant-; "
                "make one at platform.claude.com/settings/keys). Keys for other providers are never used automatically."
            )
        if configured_url and (explicit == ANTHROPIC or is_anthropic_host(configured_url)):
            base_url = _anthropic_root(configured_url)
        else:
            if configured_url:
                notes.append(
                    f"Ignoring CLIPPER_BASE_URL ({_host(configured_url)}): the key is for Claude, so it is only sent to "
                    f"Anthropic. Delete that line from .env to stop seeing this."
                )
            base_url = ANTHROPIC_URL
        if configured_model and (explicit == ANTHROPIC or configured_model.lower().startswith("claude")):
            model = configured_model
        else:
            if configured_model:
                notes.append(
                    f"Ignoring CLIPPER_MODEL={configured_model}: it is not a Claude model, so {DEFAULT_MODEL} is used. "
                    "Delete that line from .env, or name a Claude model such as claude-sonnet-5-5 or claude-haiku-5-5 "
                    "(both cheaper)."
                )
            model = DEFAULT_MODEL
        return AIConfig(api_key, base_url, model, ANTHROPIC, effort, tuple(notes))

    # an OpenAI-compatible service: it has to be named, because there is no sensible default one
    if not configured_url:
        raise ConfigError(
            "CLIPPER_PROVIDER=openai needs CLIPPER_BASE_URL: the address of the OpenAI-compatible service to use "
            "(and CLIPPER_MODEL, the model it should run)."
        )
    api_key = clipper_key
    if not api_key and _is_dashscope(configured_url):
        api_key = _clean(env.get("DASHSCOPE_API_KEY"))
    if not api_key:
        raise ConfigError(
            "No API key configured. Set CLIPPER_API_KEY in .env (see .env.example). "
            "Keys for other providers are never used automatically."
        )
    if looks_like_anthropic_key(api_key):
        raise ConfigError(
            f"CLIPPER_API_KEY is a Claude key (it starts with {ANTHROPIC_KEY_PREFIX}), and CLIPPER_PROVIDER=openai would send "
            f"it to {_host(configured_url)}. A Claude key is only sent to Anthropic: remove CLIPPER_PROVIDER, or use that "
            "service's own key."
        )
    model = configured_model or (ALIBABA_DEFAULT_MODEL if _is_dashscope(configured_url) else "")
    if not model:
        raise ConfigError(f"CLIPPER_MODEL is not set. Name the model that {_host(configured_url)} should run.")
    if effort:
        notes.append("Ignoring CLIPPER_EFFORT: it only applies to Claude.")
    return AIConfig(api_key, configured_url, model, OPENAI, None, tuple(notes))
