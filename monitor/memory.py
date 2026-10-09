"""The memory service, looked at and never changed.

There is one way to reach the service in this file, ``_get``, and it only reads. The bot's own
client can write and retire facts; none of that is used here.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import aiohttp

# How the bot words the context of a batch; the channel's name is taken from it.
_CHANNEL = re.compile(r"#(.+?) channel of a Discord server")
OWN = "-own"  # the ending of the id of a batch's second document, her own lines
FACT_KINDS = ("world", "experience")  # what the service calls its facts; "observation" is a merged summary
NAMES_FOR = 300  # seconds a look for channel names stays good

# (url, params, timeout in seconds) -> (HTTP status, parsed JSON or None)
Get = Callable[[str, Optional[dict], float], Awaitable[tuple[int, Any]]]


class Unavailable(Exception):
    """The service did not answer, or not with what was asked for."""


def _fact(row: dict) -> dict:
    entities = row.get("entities")
    if isinstance(entities, str):
        entities = [name.strip() for name in entities.split(",") if name.strip()]
    return {"id": row.get("id"), "text": row.get("text") or "", "type": row.get("fact_type") or row.get("type"),
            "document": row.get("document_id"), "at": row.get("mentioned_at") or row.get("date"),
            "state": row.get("state") or "valid", "reason": row.get("invalidation_reason"),
            "retired_at": row.get("invalidated_at"), "entities": entities or [], "tags": row.get("tags") or [],
            "merged": bool(row.get("consolidated_at"))}


class Memory:
    def __init__(self, base_url: str, *, seconds: float = 8.0, get: Optional[Get] = None,
                 clock: Callable[[], float] = time.monotonic, api_key: Optional[str] = None):
        self._root = base_url.rstrip("/")
        # The service may ask for a key. It comes from this program's environment only, never from
        # the bot's file of secrets. With none set, no header is sent at all.
        key = os.environ.get("HINDSIGHT_API_KEY", "").strip() if api_key is None else api_key
        self._headers = {"Authorization": f"Bearer {key}"} if key else None
        self._seconds = seconds
        self._get_json = get or self._request
        self._clock = clock
        self._session: Optional[aiohttp.ClientSession] = None
        self._names: dict[str, tuple[float, dict[str, str]]] = {}  # bank -> (when read, channel id -> name)

    async def _request(self, url: str, params: Optional[dict], timeout: float) -> tuple[int, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False, headers=self._headers)
        async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            try:
                data = await response.json(content_type=None)
            except ValueError:
                data = None
            return response.status, data

    async def _get(self, path: str, params: Optional[dict] = None, *, missing_ok: bool = False) -> Any:
        if not self._root:
            raise Unavailable("no memory service is set")
        try:
            status, data = await self._get_json(self._root + path, params, self._seconds)
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise Unavailable(type(exc).__name__) from None
        if status == 404 and missing_ok:
            return None
        if status != 200:
            raise Unavailable(f"http_{status}")
        return data

    def _bank(self, bank: str) -> str:
        return "/v1/default/banks/" + quote(bank, safe="")

    async def health(self) -> dict:
        data = await self._get("/health")
        return data if isinstance(data, dict) else {}

    async def stats(self, bank: str) -> dict:
        data = await self._get(self._bank(bank) + "/stats") or {}
        return {key: data.get(key) for key in (
            "total_nodes", "total_documents", "total_observations", "nodes_by_fact_type", "pending_operations",
            "failed_operations", "pending_consolidation", "failed_consolidation", "last_memory_write_at",
            "last_consolidated_at")}

    async def facts(self, bank: str, *, q: str = "", document: str = "", state: str = "", kind: str = "",
                    limit: int = 50, offset: int = 0) -> dict:
        """Facts in the bank, newest first as the service lists them. ``state`` "invalidated" lists the retired ones."""
        params = {"limit": str(limit), "offset": str(offset)}
        for name, value in (("q", q), ("document_id", document), ("state", state), ("type", kind)):
            if value:
                params[name] = value
        data = await self._get(self._bank(bank) + "/memories/list", params) or {}
        return {"total": data.get("total") or 0,
                "items": [_fact(row) for row in data.get("items") or [] if isinstance(row, dict)]}

    async def facts_only(self, bank: str, *, q: str = "", document: str = "", state: str = "", limit: int = 50,
                         offset: int = 0) -> dict:
        """Facts without the merged summaries, which the bot never shows her either.

        The service lists one kind at a time, so its two kinds of fact are joined here: all of
        the first kind, then the second.
        """
        world = await self.facts(bank, q=q, document=document, state=state, kind=FACT_KINDS[0], limit=limit, offset=offset)
        room = limit - len(world["items"])
        experience = await self.facts(bank, q=q, document=document, state=state, kind=FACT_KINDS[1],
                                      limit=max(room, 1), offset=max(0, offset - world["total"]))
        return {"total": world["total"] + experience["total"],
                "items": world["items"] + (experience["items"][:room] if room > 0 else [])}

    async def drawn(self, bank: str, document: str) -> dict:
        """What became of one document: the facts kept from it and the facts retired."""
        kept = await self.facts_only(bank, document=document, limit=100)
        retired = await self.facts_only(bank, document=document, state="invalidated", limit=100)
        # Checked again here: a listing that ignored the document would show another batch's facts.
        return {"kept": [fact for fact in kept["items"] if fact["document"] == document],
                "retired": [fact for fact in retired["items"] if fact["document"] == document]}

    async def document(self, bank: str, document: str) -> Optional[dict]:
        data = await self._get(self._bank(bank) + "/documents/" + quote(document, safe=""), missing_ok=True)
        if not isinstance(data, dict):
            return None
        return {"id": data.get("id"), "created": data.get("created_at"), "updated": data.get("updated_at"),
                "facts": data.get("memory_unit_count") or 0,
                "context": (data.get("retain_params") or {}).get("context") or ""}

    async def channel_names(self, bank: str) -> dict[str, str]:
        """Channel names, read out of what the bot told the service about each batch.

        The bot records channels by id only. It names the channel when it hands a batch of chat
        to the service, so a channel has a name here once it has had a batch.
        """
        kept = self._names.get(bank)
        if kept is not None and self._clock() - kept[0] < NAMES_FOR:
            return kept[1]
        found: dict[str, str] = {}
        try:
            offset = 0
            while offset < 2000:
                data = await self._get(self._bank(bank) + "/documents", {"limit": "200", "offset": str(offset)}) or {}
                items = data.get("items") or []
                for item in sorted(items, key=lambda one: str(one.get("created_at") or "")):
                    parts = str(item.get("id") or "").split("-")
                    named = _CHANNEL.search((item.get("retain_params") or {}).get("context") or "")
                    if len(parts) >= 3 and parts[1].isdigit() and named:
                        found[parts[1]] = named.group(1)
                offset += len(items)
                if not items or offset >= (data.get("total") or 0):
                    break
        except Unavailable:
            found = dict(kept[1]) if kept is not None else {}
        self._names[bank] = (self._clock(), found)
        return found

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
