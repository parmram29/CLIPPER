"""Runtime configuration, read from the environment (optionally a ``.env`` file).

Not part of the upstream yt-short-clipper code; added for the headless CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

# Alibaba Model Studio's OpenAI-compatible endpoint (Singapore / international).
# API keys are region-specific; the Beijing endpoint is
# https://dashscope.aliyuncs.com/compatible-mode/v1
# Docs: https://www.alibabacloud.com/help/en/model-studio/text-generation
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


class ConfigError(Exception):
    """Raised when required configuration is missing or unusable."""


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    base_url: str
    model: str

    def as_dict(self) -> dict[str, str]:
        """The ``ai`` dict shape the upstream pipeline functions expect."""
        return {"api_key": self.api_key, "base_url": self.base_url, "model": self.model}

    @property
    def masked_key(self) -> str:
        return "..." + self.api_key[-4:] if len(self.api_key) > 8 else "***"


def load_dotenv_if_present(path: str | Path | None = None) -> bool:
    """Load ``.env`` from the current directory (or ``path``) without overriding
    variables that are already set. Returns True if a file was read."""
    env_path = Path(path) if path else Path.cwd() / ".env"
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(env_path, override=False)
    return True


def _is_dashscope(base_url: str) -> bool:
    host = urlparse(base_url).hostname or ""
    return host.endswith("aliyuncs.com")


def load_ai_config(env: Mapping[str, str] | None = None) -> AIConfig:
    """Build the AI config from ``CLIPPER_*`` variables.

    ``DASHSCOPE_API_KEY`` is accepted as an alias for the key, but only when the
    base URL is an Alibaba endpoint. A key meant for another provider (e.g.
    ``OPENAI_API_KEY``) is never picked up, so it cannot be sent to the wrong host.
    """
    env = os.environ if env is None else env

    base_url = (env.get("CLIPPER_BASE_URL") or DEFAULT_BASE_URL).strip()
    model = (env.get("CLIPPER_MODEL") or DEFAULT_MODEL).strip()

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"CLIPPER_BASE_URL is not a valid http(s) URL: {base_url!r}")

    api_key = (env.get("CLIPPER_API_KEY") or "").strip()
    if not api_key and _is_dashscope(base_url):
        api_key = (env.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise ConfigError(
            "No API key configured. Set CLIPPER_API_KEY in .env (see .env.example). "
            "Keys for other providers are never used automatically."
        )

    return AIConfig(api_key=api_key, base_url=base_url, model=model)
