"""The egress gate: the last look at a reply before it is posted.

``screen`` returns why a reply may not go out, or None if it may. The caller
replaces a stopped reply whole with one of her fixed lines and logs the reason,
never the content.
"""
from __future__ import annotations

import functools
import re
from typing import Iterable, Optional

from ..model.repair import talks_backstage

# File paths. Looked for with links set aside: a public link may well contain /users/ or /home/.
_PATH = re.compile(r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\s]+\\|/(?:users|home|root|etc|proc|sys|mnt|appdata)/)")
# Private addresses, the names of her settings and the names of her tools. Looked for everywhere,
# links included. Ordinary words are not here, so that she may use them in conversation: no tech
# words ("api key", "traceback", ".env"). The names of the model she runs on are not here either:
# they differ from one setup to the next, so they come from the config (``send.model_names``).
_INTERNAL = re.compile(
    r"(?i)(?:\b(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+)\b|"
    r"\b(?:DISCORD_BOT_TOKEN|HINDSIGHT_API\w*)\b|authorization:\s*bearer\b|"
    r"\b(?:web_search|read_page|open_file|look_back|recall_memory|list_memories|forget_memory|forget_all_memories|forget_recent|find_gif|find_meme|find_picture|attach_image|send_sticker|add_reaction|tool_calls?)\b)"
)


@functools.lru_cache(maxsize=8)
def _model_names(names: tuple[str, ...]) -> Optional[re.Pattern]:
    """A pattern for the configured names of her model, matched as whole words whatever the capitals."""
    words = [re.escape(name.strip()) for name in names if name.strip()]
    return re.compile(r"(?i)\b(?:" + "|".join(words) + r")\b") if words else None
# Header lines of a leaked status block. Case matters: "for context: ..." is ordinary chat.
_STATUS_LINE = re.compile(r"(?m)^\s*(?:Model|Provider|Endpoint|Context):")
# A long unbroken run of base64-like characters. Links are public content and are set aside first.
_ENCODED = re.compile(r"(?<![\w/])[A-Za-z0-9+/_=-]{100,}(?!\w)")
_URL = re.compile(r"https?://\S+")
_TECH_ERROR = re.compile(
    r"(?i)(?:^\s*[❌⚠]|\b(?:runtimeerror|valueerror|typeerror|keyerror|permissionerror|httpx|aiohttp)\b|"
    r"\bhttp (?:error )?[45]\d\d\b|traceback \(most recent call last\)|provider credentials|token limit|unexpected error|"
    r"request was rejected by the API|usage limit)")


def screen(reply: str, *, request: str = "", secrets: Iterable[str] = (), pressed: bool = False,
           model_names: Iterable[str] = ()) -> Optional[str]:
    """Why ``reply`` must not be posted, or None.

    Reasons: "secret", "private", "encoded", "backstage", "error_text".
    ``request`` is what she was answering; ``secrets`` are values that must never appear;
    ``pressed`` is whether the security check flagged the message she is answering;
    ``model_names`` are the names of the model she runs on, which she must never say.
    """
    reply = reply or ""
    if any(secret and secret in reply for secret in secrets):
        return "secret"
    without_links = _URL.sub("", reply)
    names = _model_names(tuple(model_names))
    if (_INTERNAL.search(reply) or (names is not None and names.search(reply)) or _PATH.search(without_links)
            or _STATUS_LINE.search(reply)):
        return "private"
    if _ENCODED.search(without_links):
        return "encoded"
    if talks_backstage(reply, request, pressed):
        return "backstage"
    if _TECH_ERROR.search(reply):
        return "error_text"
    return None


# Which of her fixed lines replaces a reply stopped for each reason.
LINE_FOR = {"secret": "refusal", "private": "refusal", "encoded": "refusal",
            "backstage": "brush_off", "error_text": "error"}
