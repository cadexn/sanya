"""Memory tools: what she can look up, list and forget on request.

There is no tool for saving: facts come from ambient memory only. Listing and
forgetting are limited to the member concerned and the owner. "Forgetting"
retires a fact in the memory service: it no longer comes back in recall, but
the service keeps it in its archive. What a member has only just said has not
reached memory yet; forgetting that means holding it back, so it never does.
"""
from __future__ import annotations

import json
from typing import Any

from .. import events
from ..memory.hindsight import Fact, Hindsight, MemoryUnavailable
from ..memory.recall import is_about
from ..store import recalled, room_log
from ..store.db import Store
from .web import ToolFailure

# Given back with every forgetting. A fact is retired, not destroyed, and the chat it was said in is still there.
SAY = "say it is out of your head, or that you have dropped it. never 'deleted', 'erased' or 'wiped'"
SEARCH_LIMIT = 500  # facts looked through for one name


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


_ABOUT = {"about": {"type": "string",
                    "description": "Whose facts. Leave it out for the person you are talking to."}}
SCHEMAS = [
    _function("recall_memory", "Look in your memory for something specific.",
              {"query": {"type": "string", "description": "What to look for."}}, ["query"]),
    _function("list_memories", "List what you remember about the person you are talking to, or about someone "
              "they name.", _ABOUT, []),
    _function("forget_memory", "Forget one remembered fact, when the person it is about asks you to.",
              {"memory": {"type": "string", "description": "The fact's id. Get it from list_memories or recall_memory "
                                                           "first; never guess it."}},
              ["memory"]),
    _function("forget_all_memories", "Forget everything you remember about the person you are talking to, "
              "when they ask you to.", _ABOUT, []),
    _function("forget_recent", "Keep what the person you are talking to has just said in this channel out of your "
              "memory. For when they ask you to forget something they said a moment ago and you cannot find it in "
              "your memory: it has not got there yet, and this stops it.", {}, []),
]


def _shown(facts: list[Fact]) -> list[dict]:
    return [{"id": fact.id, "text": " ".join(fact.text.split())} for fact in facts]


class MemoryTools:
    def __init__(self, hindsight: Hindsight, store: Store, recall_facts: int, list_facts: int):
        self._hindsight = hindsight
        self._store = store
        self._recall_facts = recall_facts
        self._list_facts = list_facts  # facts shown in one listing

    def _bank(self, context: Any) -> str:
        if not context.bank:
            raise ToolFailure("memory_unavailable")
        return context.bank

    def _whose(self, arguments: dict, context: Any, anyone: bool = False) -> tuple[str, ...]:
        """The names to act on: the requester's own, or someone else's.

        Someone else's facts may be listed for anyone who asks (``anyone``), by the owner's decision of
        9 October 2026. Having them forgotten is still for the person concerned and the owner.
        """
        named = str(arguments.get("about") or "").strip()
        own = {name.casefold() for name in context.requester_names}
        if not named or named.casefold() in own:
            return tuple(context.requester_names)
        if not (anyone or context.is_owner):
            raise ToolFailure("not_allowed")
        return (named,)

    async def _about(self, bank: str, names: tuple[str, ...]) -> list[Fact]:
        found: dict[str, Fact] = {}
        for name in names:
            for offset in range(0, SEARCH_LIMIT, 100):
                page = await self._hindsight.facts(bank, q=name, limit=100, offset=offset)
                found.update({fact.id: fact for fact in page if is_about(fact, names)})
                if len(page) < 100:
                    break
        return list(found.values())

    def _curated(self, context: Any, facts: list[Fact]) -> None:
        # A batch that has had a fact forgotten is never read again: that would bring the fact back.
        conn = self._store.guild(context.guild_id)
        for document_id in {fact.document_id for fact in facts if fact.document_id}:
            # A fact drawn from her own lines belongs to the same batch as the one drawn from the members'.
            conn.execute("UPDATE memory_batches SET curated = 1 WHERE document_id = ?",
                         (document_id.removesuffix("-own"),))

    @staticmethod
    def _seen(context: Any, tool: str, facts: list[Fact]) -> None:
        # Kept beside the reply, with the facts recalled for it, for whoever reads her records later.
        seen = getattr(context, "seen", None)
        if seen is not None:
            seen.extend((tool, fact) for fact in facts)

    def _gone(self, context: Any, facts: list[Fact]) -> None:
        """A forgotten fact goes from the copies kept beside her earlier replies, and is not kept for this one."""
        conn = self._store.guild(context.guild_id)
        recalled.safely("recalled", lambda: recalled.forget_facts(conn, [fact.id for fact in facts]))
        forgotten = getattr(context, "forgotten", None)
        if forgotten is not None:
            forgotten.update(fact.id for fact in facts)

    async def recall(self, arguments: dict, context: Any) -> tuple[str, str]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolFailure("bad_request")
        try:
            facts = await self._hindsight.recall(self._bank(context), query.strip()[:1000], self._recall_facts)
        except MemoryUnavailable:
            raise ToolFailure("memory_unavailable") from None
        self._seen(context, "recall_memory", facts)
        return json.dumps({"success": True, "memories": _shown(facts)}, ensure_ascii=False), "you searched your memory"

    async def list(self, arguments: dict, context: Any) -> tuple[str, str]:
        names = self._whose(arguments, context, anyone=True)
        try:
            facts = await self._about(self._bank(context), names)
        except MemoryUnavailable:
            raise ToolFailure("memory_unavailable") from None
        self._seen(context, "list_memories", facts[:self._list_facts])
        return (json.dumps({"success": True, "about": names[0] if names else "", "total": len(facts),
                            "memories": _shown(facts[:self._list_facts])}, ensure_ascii=False),
                "you listed what you remember about someone")

    async def forget(self, arguments: dict, context: Any) -> tuple[str, str]:
        memory_id = str(arguments.get("memory") or "").strip()
        if not memory_id:
            raise ToolFailure("bad_request")
        bank = self._bank(context)
        try:
            fact = await self._hindsight.fact(bank, memory_id)
            if fact is None:
                raise ToolFailure("no_such_memory")
            # Only someone the fact is about, or the owner, may have it forgotten.
            if not (context.is_owner or is_about(fact, context.requester_names)):
                raise ToolFailure("not_allowed")
            await self._hindsight.retire(bank, fact.id, "forgotten at a member's request")
        except MemoryUnavailable:
            raise ToolFailure("memory_unavailable") from None
        self._curated(context, [fact])
        self._gone(context, [fact])
        # The request, and the talk around it, usually say the thing again: that chat is held back too.
        held = self._hold_back(context, [context.requester_id], context.channel_id)
        return (json.dumps({"success": True, "forgotten": 1, "recent_messages_kept_out_of_memory": held, "next": SAY}),
                "you forgot one thing at their request")

    async def forget_all(self, arguments: dict, context: Any) -> tuple[str, str]:
        names = self._whose(arguments, context)
        bank = self._bank(context)
        try:
            facts = await self._about(bank, names)
            for fact in facts:
                await self._hindsight.retire(bank, fact.id, "forgotten at a member's request")
        except MemoryUnavailable:
            raise ToolFailure("memory_unavailable") from None
        self._curated(context, facts)
        self._gone(context, facts)
        # What they have said lately has not reached memory yet. It is held back, or it would arrive later.
        conn = self._store.guild(context.guild_id)
        own = names == tuple(context.requester_names)
        accounts = [context.requester_id] if own else room_log.accounts_named(conn, names[0])
        held = self._hold_back(context, accounts, None)
        return (json.dumps({"success": True, "forgotten": len(facts), "recent_messages_kept_out_of_memory": held,
                            "next": SAY}),
                "you forgot everything you remembered about someone at their request")

    def _hold_back(self, context: Any, accounts: list[str], channel_id: Any) -> int:
        held = room_log.hold_back(self._store.guild(context.guild_id), [one for one in accounts if one], channel_id)
        if held:
            events.log_event("memory_held_back", guild_id=context.guild_id, messages=held)
        return held

    async def forget_recent(self, arguments: dict, context: Any) -> tuple[str, str]:
        """Keep the requester's own not-yet-remembered messages in this channel out of memory for good."""
        self._bank(context)
        if not context.requester_id:
            raise ToolFailure("bad_request")
        held = self._hold_back(context, [context.requester_id], context.channel_id)
        return (json.dumps({"success": True, "recent_messages_kept_out_of_memory": held, "next": SAY}),
                "you kept what they had just said out of your memory")
