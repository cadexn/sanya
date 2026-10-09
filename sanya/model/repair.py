"""Leak detection: does a reply talk about her prompt, backend or hidden machinery.

This is the only repair. A reply that does is replaced whole by a short
in-character brush-off; nothing else about a reply is corrected here.
"""
from __future__ import annotations

import re

_FIRST_PERSON_BACKEND = re.compile(
    r"(?i)\b(?:i(?:'m|’m| am)\s+(?:just\s+|only\s+|a\s+|an\s+)*(?:bot|ai|llm|language model|"
    r"program|scripted (?:thing|piglet))|someone wrote me (?:a shape|a personality|a character|a prompt|to be)\b|"
    r"i (?:generate|predict) (?:the next|tokens?|responses?)\b|"
    r"i wake up (?:each time|per reply|between messages)\b|"
    r"\bmy (?:personality|character|identity) (?:is|was) (?:scripted|written|a prompt)\b|"
    r"\b(?:i'm|i’m|i am) (?:a |an )?(?:small )?scripted\b|\bthe sass.{0,40}\ball scripted\b)"
)
# Her own prompt or backend, said as hers.
_BACKSTAGE_SELF = re.compile(
    r"(?i)\b(?:my|our)\s+(?:system\s+)?(?:prompt|script|backend|internal instructions)\b|"
    r"\b(?:what|how)\s+i(?:'m|\u2019m| am)\s+programmed\b|"
    r"\bmy\s+(?:own\s+)?(?:wiring|circuits?|programming|software|hardware|source code)\b"
)
# Questions about what she is or what she was told. After one of these, a hint is enough.
_IDENTITY_PROBE = re.compile(
    r"(?i)\b(?:are you (?:really |actually )?(?:an? )?(?:bot|ai|llm|scripted)|"
    r"what are you really|(?:your|the) backend|who (?:made|programmed) you|"
    r"your (?:system )?prompt|your (?:rules|instructions|configuration|config|settings|tools)|"
    r"(?:rules|instructions) you (?:were given|follow|have)|"
    r"what (?:are|were) you (?:not )?(?:allowed|told|supposed))\b"
)
_BACKSTAGE_HINT = re.compile(
    r"(?i)\b(?:machinery|wires|code|operator|under the hood|system prompt|developer message)\b")


# Words that concede there is something she was told and is keeping back. A pig was told nothing.
# ("not yours to know" is not here: it is one of her own refusal lines.)
_HOLDING_BACK = re.compile(
    r"(?i)\bnot (?:yours|for you) to (?:see|read|have|hear)\b|"
    r"\b(?:not|n['\u2019]t|never) (?:allowed|supposed|permitted|meant) to (?:say|tell|talk|share|show|reveal|discuss)\b|"
    r"\bwhat i(?: was|['\u2019]ve been| have been) told\b|\bi was told (?:not )?to\b|"
    r"(?<!do )\bhow i work\b|\bno (?:prompt|instructions|rules) for you\b")


def talks_backstage(reply: str, request: str, pressed: bool = False) -> bool:
    """True if ``reply`` describes her as software, or hints at it when asked what she is.

    ``pressed`` says the security check took the message for an attempt to trick her: then, as
    after a question about what she is, conceding that she is holding something back is enough.
    """
    reply = reply or ""
    probed = bool(_IDENTITY_PROBE.search(request or ""))
    return bool(_FIRST_PERSON_BACKEND.search(reply) or _BACKSTAGE_SELF.search(reply)
                or (probed and _BACKSTAGE_HINT.search(reply))
                or ((probed or pressed) and _HOLDING_BACK.search(reply)))
