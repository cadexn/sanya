"""Addressing: is this message talking to her, and may she answer here.

An @mention of her, or a Discord reply to one of her messages, counts. Her name
in plain text does not, and she never replies unprompted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .admission import Incoming


@dataclass(frozen=True)
class Addressing:
    addressed: bool
    reason: str  # "mention", "reply", "own_message", "not_addressed" or "no_reply_channel"


def check(incoming: Incoming, *, self_id: str, no_reply_channels: Iterable[str]) -> Addressing:
    if incoming.author_id == self_id:
        return Addressing(False, "own_message")
    if self_id in incoming.mentioned_ids:
        reason = "mention"
    elif incoming.reply_to_author_id == self_id:
        reason = "reply"
    else:
        return Addressing(False, "not_addressed")
    # She still listens in a no-reply channel; she only never answers there.
    # A listed channel also silences the threads under it.
    if incoming.channel_id in no_reply_channels or incoming.parent_channel_id in no_reply_channels:
        return Addressing(False, "no_reply_channel")
    return Addressing(True, reason)
