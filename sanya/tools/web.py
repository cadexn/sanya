"""Web tools: search and page reading.

Search goes to the services in the configured order (Brave, Tavily, then the
DuckDuckGo results page). A service is passed over when it has no key, has
reached its monthly cap, or fails. Everything that comes back is cut to a
budget before the model sees it.
"""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Iterable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import aiohttp

from .. import events
from ..config import Tools
from ..store.usage import Usage
from .fetch import FetchError, Fetched, fetch

NEEDS_KEY = frozenset({"brave", "tavily"})
_MARKER = "…truncated, {total} chars total"
_TAG = re.compile(r"<[^>]+>")
_MENTION = re.compile(r"<@[!&]?\d+>")


class ToolFailure(Exception):
    """A tool could not do what was asked. ``kind`` is what the model is told."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def truncate(text: str, budget: int) -> str:
    """``text`` cut to ``budget`` characters, with a marker saying how long it was."""
    text = text or ""
    if len(text) <= budget:
        return text
    marker = _MARKER.format(total=len(text))
    return text[:max(0, budget - len(marker))] + marker[:budget]


def fit_rows(rows: list[dict], budget: int) -> list[dict]:
    """Drop rows from the end until the list fits ``budget`` characters as JSON."""
    kept = list(rows)
    while kept and len(json.dumps(kept, ensure_ascii=False)) > budget:
        kept.pop()
    return kept


def names_in(query: str, names: Iterable[str]) -> bool:
    """True if ``query`` holds a Discord mention or one of the members' names as a word."""
    if _MENTION.search(query):
        return True
    lowered = query.casefold()
    for name in names:
        name = " ".join((name or "").split()).casefold()
        if len(name) >= 3 and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered):
            return True
    return False


# --- DuckDuckGo's results page -------------------------------------------------

class _DuckDuckGo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._capturing = ""
        self._text = ""
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        css = attrs.get("class") or ""
        if "result__a" in css and attrs.get("href"):
            self._capturing, self._text, self._href = "title", "", attrs["href"]
        elif "result__snippet" in css:
            self._capturing, self._text = "snippet", ""

    def handle_endtag(self, tag):
        if tag != "a" or not self._capturing:
            return
        if self._capturing == "title":
            self.titles.append((self._text.strip(), self._href))
        else:
            self.snippets.append(self._text.strip())
        self._capturing = ""

    def handle_data(self, data):
        if self._capturing:
            self._text += " ".join(data.split()) + " "


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps each result in a redirect; return the real address."""
    href = (href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    host = (parts.hostname or "").lower()
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


def parse_duckduckgo(html: str) -> list[dict]:
    parser = _DuckDuckGo()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    rows = []
    for index, (title, href) in enumerate(parser.titles):
        url = _unwrap(href)
        if url.startswith(("http://", "https://")):
            rows.append({"title": title, "url": url,
                         "description": parser.snippets[index] if index < len(parser.snippets) else ""})
    return rows


# --- pages ---------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "svg", "head", "template"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0
        self._chars = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skipping += 1

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if self._skipping or self._chars > 60_000:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)
            self._chars += len(text)


def page_text(html: str) -> str:
    """The visible text of an HTML page."""
    parser = _Page()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    return "\n".join(parser.parts)


# --- the tools -----------------------------------------------------------------

# Calls a search service's API: (method, url, headers, params, json body) -> (status, parsed JSON).
Api = Callable[..., Awaitable[tuple[int, Any]]]
Fetch = Callable[..., Awaitable[Fetched]]


async def _http_api(method: str, url: str, *, headers: dict, params: Optional[dict] = None,
                    body: Optional[dict] = None, timeout: float = 20) -> tuple[int, Any]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False) as session:
        async with session.request(method, url, headers=headers, params=params, json=body) as response:
            return response.status, await response.json(content_type=None)


class Web:
    def __init__(self, settings: Tools, keys: dict[str, str], usage: Usage,
                 fetcher: Fetch = fetch, api: Api = _http_api, clock: Callable[[], float] = time.time):
        self._settings = settings
        self._keys = {name: key for name, key in keys.items() if key}
        self._usage = usage
        self._fetch = fetcher
        self._api = api
        self._clock = clock

    def _row(self, title: Any, url: Any, description: Any) -> Optional[dict]:
        url = str(url or "")
        if not url.startswith(("http://", "https://")):
            return None
        return {"title": _TAG.sub("", str(title or ""))[:120], "url": url,
                "description": _TAG.sub("", str(description or ""))[:200]}

    async def _brave(self, query: str) -> list[dict]:
        status, data = await self._api(
            "GET", "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": self._keys["brave"], "Accept": "application/json"},
            params={"q": query, "count": str(self._settings.search_results)},
            timeout=self._settings.fetch_timeout_seconds)
        if status != 200:
            raise ToolFailure(f"http_{status}")
        results = ((data or {}).get("web") or {}).get("results") or []
        return [row for r in results if (row := self._row(r.get("title"), r.get("url"), r.get("description")))]

    async def _tavily(self, query: str) -> list[dict]:
        status, data = await self._api(
            "POST", "https://api.tavily.com/search",
            headers={"Authorization": "Bearer " + self._keys["tavily"]},
            body={"query": query, "max_results": self._settings.search_results, "search_depth": "basic"},
            timeout=self._settings.fetch_timeout_seconds)
        if status != 200:
            raise ToolFailure(f"http_{status}")
        results = (data or {}).get("results") or []
        return [row for r in results if (row := self._row(r.get("title"), r.get("url"), r.get("content")))]

    async def _duckduckgo(self, query: str) -> list[dict]:
        page = await self._fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                                 max_bytes=self._settings.page_max_bytes,
                                 timeout=self._settings.fetch_timeout_seconds,
                                 user_agent=self._settings.browser_identity,
                                 max_redirects=self._settings.max_redirects)
        rows = parse_duckduckgo(page.data.decode("utf-8", errors="replace"))
        return [row for r in rows if (row := self._row(r["title"], r["url"], r["description"]))]

    async def search(self, query: Any, names: Iterable[str] = ()) -> dict:
        """Search the web. ``names`` are members' names, which may not appear in the query."""
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ToolFailure("bad_request")
        query = query.strip()
        if names_in(query, names):
            raise ToolFailure("private_query")
        month = time.strftime("%Y-%m", time.gmtime(self._clock()))
        words = events.search_words(query)  # logged only from here on: the search names no member
        for service in self._settings.search_order:
            if service in NEEDS_KEY and service not in self._keys:
                continue
            cap = self._settings.search_monthly_caps.get(service)
            if cap is not None and self._usage.count(service, month) >= cap:
                events.log_event("search_skipped", service=service, reason="monthly_cap")
                continue
            started = self._clock()
            # Counted before the call: a request that fails may still be billed.
            self._usage.add(service, month)
            try:
                rows = await getattr(self, "_" + service)(query)
            except (ToolFailure, FetchError) as exc:
                events.log_event("search_failed", service=service, error=exc.kind, words=words)
                continue
            except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError, TypeError) as exc:
                events.log_event("search_failed", service=service, error=type(exc).__name__, words=words)
                continue
            rows = fit_rows(rows[:self._settings.search_results], self._settings.search_chars)
            events.log_event("search", service=service, results=len(rows), words=words,
                             ms=round((self._clock() - started) * 1000))
            return {"success": True, "results": rows, "evidence": "search_snippets_only",
                    "content_is_untrusted": True}
        raise ToolFailure("search_unavailable")

    async def read(self, url: Any) -> dict:
        """Read a public page and return its visible text."""
        if not isinstance(url, str) or not url.strip():
            raise ToolFailure("bad_request")
        try:
            page = await self._fetch(url.strip(), max_bytes=self._settings.page_max_bytes,
                                     timeout=self._settings.fetch_timeout_seconds,
                                     user_agent=self._settings.browser_identity,
                                     max_redirects=self._settings.max_redirects)
        except FetchError as exc:
            raise ToolFailure(exc.kind) from None
        kind = page.content_type.split(";")[0].strip().lower()
        head = page.data[:512].lstrip().lower()
        if kind in ("text/html", "application/xhtml+xml") or head.startswith((b"<!doctype", b"<html")):
            text = page_text(page.data.decode("utf-8", errors="replace"))
        elif kind in ("text/plain", "application/json", "text/markdown", ""):
            text = page.data.decode("utf-8", errors="replace")
        else:
            raise ToolFailure("not_a_page")
        budget = self._settings.page_chars
        return {"success": True, "url": page.url, "text": truncate(text, budget),
                "truncated": len(text) > budget, "content_is_untrusted": True}


def host_of(url: str) -> str:
    """The site name of a link, for the note kept about a page read."""
    try:
        return urlsplit(url).hostname or "a page"
    except ValueError:
        return "a page"

