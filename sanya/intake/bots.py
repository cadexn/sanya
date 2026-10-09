"""The bot exchange rule: how she answers other bots.

She answers a bot's message as a normal reply. If the bot responds, she
answers once more as a plain message, without replying to or tagging it. The
bot's next message starts a new exchange. So her answers to one bot in one
channel alternate: reply, plain, reply, plain.

The ceiling, when set, is how many times in a row she will answer bots in a
channel before a person has spoken there. The bot accounts listed as people in
the config (``discord.bots_as_people``) are exempt from both.
"""
from __future__ import annotations

from typing import Iterable, Optional


class BotExchanges:
    def __init__(self, exempt: Iterable[str], ceiling: Optional[int]):
        self._exempt = frozenset(exempt)
        self._ceiling = ceiling  # None = no ceiling
        self._plain_next: dict[tuple[str, str], bool] = {}
        self._answered: dict[str, int] = {}  # channel -> answers to bots since a person spoke

    def person_spoke(self, channel_id: str) -> None:
        self._answered.pop(channel_id, None)

    def may_answer(self, channel_id: str, bot_id: str) -> bool:
        if bot_id in self._exempt or self._ceiling is None:
            return True
        return self._answered.get(channel_id, 0) < self._ceiling

    def take(self, channel_id: str, bot_id: str) -> str:
        """Record one answer to this bot and return its form: "reply" or "plain"."""
        if bot_id in self._exempt:
            return "reply"
        self._answered[channel_id] = self._answered.get(channel_id, 0) + 1
        key = (channel_id, bot_id)
        plain = self._plain_next.get(key, False)
        self._plain_next[key] = not plain
        return "plain" if plain else "reply"
