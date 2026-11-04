"""Named highlight-prompt profiles.

``viral`` is CLIPPER's default (drama, arguments, scandal, wild and WTF moments, punchlines,
roasts, spicy-but-appropriate, emotion). ``podcast`` is upstream's original prompt, tuned for
podcast footage. ``--prompt-file`` overrides either.
"""

from __future__ import annotations

from .prompts import DEFAULT_HIGHLIGHT_PROMPT
from .viral_prompt import VIRAL_HIGHLIGHT_PROMPT

DEFAULT_PROFILE = "viral"
PROFILE_PROMPTS = {
    "viral": VIRAL_HIGHLIGHT_PROMPT,
    "podcast": DEFAULT_HIGHLIGHT_PROMPT,
}
PROFILE_NAMES = tuple(PROFILE_PROMPTS)


def get_profile_prompt(name: str) -> str:
    try:
        return PROFILE_PROMPTS[name]
    except KeyError:
        raise ValueError(
            f"unknown profile {name!r}; choose one of: {', '.join(PROFILE_NAMES)}"
        ) from None
