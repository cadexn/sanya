"""The Hindsight client: the only code that talks to the memory service.

Hindsight keeps facts, not chat. It has no way to delete one fact; a fact can
be retired, which hides it from recall and moves it to the service's archive.

When asked to merge, the service also writes summaries of its facts
("observations"). By the owner's decision she is never shown those: a summary
doubles up with the facts it was made from, and can say more than was said.
Everything here deals in the facts themselves. A summary goes when the facts
it was made from are retired or deleted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import aiohttp

FACT_TYPES = ["world", "experience"]  # what the service calls its facts; "observation" is a merged summary


class MemoryUnavailable(Exception):
    """The memory service did not do what was asked. ``kind`` is safe to log and to tell the model."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    entities: tuple[str, ...] = ()  # the names the service attached to the fact
    document_id: Optional[str] = None
    mentioned_at: Optional[str] = None  # ISO 8601


def _names(value: Any) -> tuple[str, ...]:
    """The service gives entities as a list in one place and a comma-separated string in another."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return ()
    names = [(item.get("name") or item.get("canonical_name") or "") if isinstance(item, dict) else str(item)
             for item in value]
    return tuple(name.strip() for name in names if name and name.strip())


def _fact(row: Any) -> Optional[Fact]:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    if "observation" in (row.get("type"), row.get("fact_type")):
        return None  # a merged summary, not a fact
    return Fact(str(row["id"]), str(row.get("text") or ""), _names(row.get("entities")),
                row.get("document_id"), row.get("mentioned_at"))


# (method, url, params, json body, timeout in seconds) -> (HTTP status, parsed JSON or None)
Http = Callable[..., Awaitable[tuple[int, Any]]]


class Hindsight:
    def __init__(self, base_url: str, *, recall_seconds: float, read_seconds: float, write_seconds: float,
                 http: Optional[Http] = None, api_key: str = ""):
        self._base = base_url.rstrip("/") + "/v1/default/banks/"
        self._http = http or self._request
        # The service may ask for a key. With none set, no header is sent at all.
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        # Time limits: recall before a reply, a listing or look-up, and a write, which waits for the model server.
        self._recall_seconds, self._read_seconds, self._write_seconds = recall_seconds, read_seconds, write_seconds
        self._session: Optional[aiohttp.ClientSession] = None

    async def _request(self, method: str, url: str, *, params: Optional[dict], body: Optional[dict],
                       timeout: float) -> tuple[int, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False, headers=self._headers)
        async with self._session.request(method, url, params=params, json=body,
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            try:
                data = await response.json(content_type=None)
            except ValueError:
                data = None
            return response.status, data

    async def _call(self, method: str, bank: str, path: str, *, params: Optional[dict] = None,
                    body: Optional[dict] = None, timeout: Optional[float] = None, missing_ok: bool = False) -> Any:
        try:
            status, data = await self._http(method, self._base + bank + path, params=params, body=body,
                                            timeout=self._read_seconds if timeout is None else timeout)
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise MemoryUnavailable(type(exc).__name__) from None
        if status in (400, 404, 422) and missing_ok:
            return None  # not there, or not even a possible id
        if status != 200:
            raise MemoryUnavailable(f"http_{status}")
        return data

    async def recall(self, bank: str, query: str, limit: int) -> list[Fact]:
        """The facts most relevant to ``query``, best first. No model call is involved."""
        data = await self._call("POST", bank, "/memories/recall", timeout=self._recall_seconds, body={
            "query": query, "types": FACT_TYPES, "max_tokens": 2048, "budget": "low", "include": {"entities": None}})
        facts = [fact for row in (data or {}).get("results") or [] if (fact := _fact(row))]
        return facts[:limit]

    async def retain(self, bank: str, content: str, *, document_id: str, context: str, timestamp: str,
                     tags: list[str]) -> None:
        """Hand a piece of chat to the service to draw facts from. Blocks until it is done.

        A document id that already exists is replaced: its old facts go and the
        new text is read afresh.
        """
        await self._call("POST", bank, "/memories", timeout=self._write_seconds, body={"items": [{
            "content": content, "document_id": document_id, "context": context, "timestamp": timestamp,
            "tags": tags}]})

    async def facts(self, bank: str, *, q: Optional[str] = None, document_id: Optional[str] = None,
                    limit: int = 50, offset: int = 0) -> list[Fact]:
        """Facts in the bank, optionally only those containing ``q`` or drawn from one document."""
        params = {"limit": str(limit), "offset": str(offset)}
        if q:
            params["q"] = q
        if document_id:
            params["document_id"] = document_id
        data = await self._call("GET", bank, "/memories/list", params=params)
        return [fact for row in (data or {}).get("items") or [] if (fact := _fact(row))]

    async def fact(self, bank: str, memory_id: str) -> Optional[Fact]:
        return _fact(await self._call("GET", bank, f"/memories/{memory_id}", missing_ok=True))

    async def retire(self, bank: str, memory_id: str, reason: str) -> None:
        """Hide a fact from recall. The service keeps it in its archive, where it could be restored."""
        await self._call("PATCH", bank, f"/memories/{memory_id}", body={"state": "invalidated", "reason": reason})

    async def delete_document(self, bank: str, document_id: str) -> None:
        """Delete a document and every fact drawn from it."""
        await self._call("DELETE", bank, f"/documents/{document_id}", missing_ok=True)

    async def merge(self, bank: str) -> None:
        """Ask the service to merge recent facts into summaries, in its own background."""
        await self._call("POST", bank, "/consolidate")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
