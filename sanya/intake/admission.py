"""Admission: which Discord events are let in at all.

Admitted means "recorded and considered", not "answered". Whether she replies
is decided later, by addressing.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, Optional

# Ordinary messages and replies. Joins, pins, boosts and the rest are not chat.
CHAT_KINDS = frozenset({"default", "reply"})


@dataclass(frozen=True)
class Incoming:
    """One Discord message, reduced to what the bot needs. Ids are strings."""

    message_id: str
    guild_id: Optional[str]  # None for a direct message
    channel_id: str
    author_id: str
    author_is_bot: bool
    kind: str  # "default", "reply", or "other"
    text: str
    author_name: str = ""
    mentioned_ids: tuple[str, ...] = ()  # members @mentioned in the message
    reply_to_author_id: Optional[str] = None  # who wrote the message this one replies to
    reply_to_message_id: Optional[str] = None  # the message this one replies to
    readable_text: str = ""  # the text with mentions shown as names
    attachment_count: int = 0
    parent_channel_id: Optional[str] = None  # for a thread, the channel it hangs under


@dataclass(frozen=True)
class Decision:
    admitted: bool
    reason: str  # "ok", "dm", "server_not_active", "not_chat" or "duplicate"


class Admission:
    """Drops direct messages, servers she is not switched on in, non-chat events and repeats."""

    def __init__(self, active_servers: Optional[Iterable[str]] = None, remember: int = 5000):
        # None means every server she is in.
        self._active = None if active_servers is None else frozenset(active_servers)
        self._remember = remember
        self._seen: OrderedDict[str, None] = OrderedDict()

    def check(self, incoming: Incoming) -> Decision:
        if incoming.guild_id is None:
            return Decision(False, "dm")
        if self._active is not None and incoming.guild_id not in self._active:
            return Decision(False, "server_not_active")
        if incoming.kind not in CHAT_KINDS:
            return Decision(False, "not_chat")
        if incoming.message_id in self._seen:
            return Decision(False, "duplicate")
        self._seen[incoming.message_id] = None
        if len(self._seen) > self._remember:
            self._seen.popitem(last=False)
        return Decision(True, "ok")
