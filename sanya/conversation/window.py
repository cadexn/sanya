"""The recency window: the part of a channel's conversation the model is sent."""
from __future__ import annotations

from ..store.history import Entry


def window(entries: list[Entry], limit: int) -> list[Entry]:
    """The last ``limit`` entries, trimmed so they open with a member's message.

    A window that opened on one of her replies would show an answer without
    the message it answered.
    """
    kept = entries[-limit:]
    while kept and kept[0].role != "user":
        kept = kept[1:]
    return kept
