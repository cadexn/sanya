"""Image tools: finding a GIF, a meme or a picture, and attaching one to her reply.

An image can be attached only if this turn's search found it and it passed
the checks: it downloaded from a public address, its bytes really are an
image, it is small enough to upload, and it has not been posted in the server
in the last day. That is the media grant.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import aiohttp

from .. import events
from ..config import Config
from ..intake.attachments import Unusable, media_type
from ..model.client import spoken
from ..store import ledger
from ..store.db import Store
from ..store.usage import Usage
from .fetch import FetchError, fetch
from .web import Api, Fetch, ToolFailure, _http_api, names_in

KLIPY = "https://api.klipy.com/api/v1/{key}/gifs/search"  # the key is part of the address: never log it
KLIPY_MEMES = "https://api.klipy.com/api/v1/{key}/static-memes/search"  # its library of still memes
KLIPY_CUSTOMER = "sanya"  # Klipy asks who is searching; it is always told the same thing, never a member
GIPHY = "https://api.giphy.com/v1/gifs/search"
BRAVE_IMAGES = "https://api.search.brave.com/res/v1/images/search"
GIPHY_QUERY_CHARS = 50  # Giphy answers a longer query with HTTP 414


def shortened(query: str, limit: int) -> str:
    """``query`` cut to ``limit`` characters, at the end of a word where there is one."""
    query = " ".join(query.split())
    if len(query) <= limit:
        return query
    return query[:limit + 1].rpartition(" ")[0] or query[:limit]


# Where each kind of image is looked for, in order: (the service, the method that asks it). The next
# one is asked when a service has no key, fails or finds nothing.
# Memes come from the image search first: tried on 9 October 2026, it found captioned memes about the
# topic, where much of Klipy's meme library was single frames taken from moving ones.
SOURCES = {
    "GIF": (("klipy", "_klipy"), ("giphy", "_giphy")),
    "meme": (("brave", "_brave_memes"), ("klipy", "_klipy_memes")),
    "picture": (("brave", "_brave"),),
}


@dataclass(frozen=True)
class Hit:
    """One search result: the image's own address, and the copy the search service keeps of it, if any."""

    title: str
    url: str
    spare: str = ""


@dataclass
class Candidate:
    number: int
    kind: str  # "GIF", "meme" or "picture"
    title: str
    url: str
    data: bytes
    mime: str
    sha: str


@dataclass
class MediaTurn:
    """What one turn may do about images."""

    mode: str  # "optional" or "requested"
    searches_left: int
    per_search: int  # candidates checked and shown to her from one search
    size_limit: int  # the biggest file the server lets her upload, in bytes
    meme: bool = False  # the member asked for a meme by that name
    searches: int = 0  # searches she has made this turn
    candidates: dict[int, Candidate] = field(default_factory=dict)
    chosen: Optional[Candidate] = None
    max_images: int = 1  # images one reply may carry: more than one only when they asked
    extra: list[Candidate] = field(default_factory=list)  # further images chosen with the first, in order
    # The server's own stickers she may send this turn, by lower-cased name: those not sent in the last day.
    stickers: dict[str, Any] = field(default_factory=dict)
    asked_sticker: bool = False  # the member asked for a sticker by that name
    sticker: Any = None  # the sticker she chose; it never goes with an image

    @property
    def tag(self) -> str:
        """The note in her prompt that says what this turn may do about images."""
        if self.meme:
            return f"[image: {self.mode}, a meme]"
        if self.asked_sticker and self.stickers:
            return f"[image: {self.mode}, a sticker]"
        return f"[image: {self.mode}]"


STICKERS_LISTED = 50  # how many of the server's stickers her tool names
STICKER_LEDGER = "sticker:{id}"  # how a sticker is named in the media ledger, where an image has its fingerprint


def sticker_schema(stickers: dict[str, Any]) -> dict:
    """The tool for sending one of the server's own stickers, which names those she may send now."""
    listed = []
    for sticker in list(stickers.values())[:STICKERS_LISTED]:
        about = " ".join(str(getattr(sticker, "description", "") or "").split())[:60]
        name = " ".join(str(sticker.name).split())
        listed.append(f"{name} ({about})" if about else name)
    return _function(
        "send_sticker", "Send one of this server's own stickers with your reply, for a feeling or a joke. "
                        "A sticker goes instead of an image, never with one. The stickers: " + "; ".join(listed) + ".",
        {"name": {"type": "string", "description": "The sticker's name, as listed."},
         "line": {"type": "string", "description": "The few words that go with the sticker, typed as you always type, "
                                                     "with no full stop at the end. This is your reply. Leave it out only "
                                                     "when the sticker needs no words."}},
        ["name"])


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


_QUERY = {"query": {"type": "string", "description": "Two or three plain words for what to look for, such as a feeling "
                                                         "or the name of a meme. Not a sentence."}}
SCHEMAS = [
    _function("find_meme", "Look for a meme: a still image with a caption, or a known joke format, about a topic. "
                           "You are shown each one found.", _QUERY, ["query"]),
    _function("find_gif", "Look for a reaction GIF: a moving image for a feeling or a response. "
                          "You are shown a still from each one found.", _QUERY, ["query"]),
    _function("find_picture", "Look for a plain picture of a thing, a person or a place. You are shown each one found.",
              _QUERY, ["query"]),
    _function("attach_image", "Attach one of the images you found to your reply, with the line that goes with it.",
              {"number": {"type": "integer", "description": "The number of the image to attach."},
               "more": {"type": "array", "items": {"type": "integer"},
                        "description": "The numbers of up to two further images to post with it. Only when they asked "
                                       "for several."},
               "line": {"type": "string", "description": "The few words that go with the image, typed as you always type, "
                                                           "with no full stop at the end. This is your reply."}},
              ["number", "line"]),
]


def preview(data: bytes, size: int) -> str:
    """A small still of an image, as the model is shown it when choosing."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        frames = getattr(image, "n_frames", 1)
        if frames > 1:
            image.seek(frames // 2)  # the middle of a GIF says more than its first frame
        still = image.convert("RGB")
        still.thumbnail((size, size))
        buffer = io.BytesIO()
        still.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class Media:
    def __init__(self, config: Config, keys: dict[str, str], usage: Usage, store: Store,
                 fetcher: Fetch = fetch, api: Api = _http_api, clock: Callable[[], float] = time.time):
        self._config = config
        self._keys = {name: key for name, key in keys.items() if key}
        self._usage = usage
        self._store = store
        self._fetch = fetcher
        self._api = api
        self._clock = clock

    @property
    def available(self) -> bool:
        """Is there any image service she can use."""
        return bool(self._keys)

    async def _klipy_search(self, address: str, query: str, sizes: tuple[str, ...], formats: tuple[str, ...],
                            untitled: str) -> list[Hit]:
        """One of Klipy's libraries. Of each image, the first of ``sizes`` that has one of ``formats`` is taken."""
        if "klipy" not in self._keys:
            raise ToolFailure("search_unavailable")
        wanted = self._config.media.search_results
        # Klipy gives a page of 8 to 50. Rating "r" is its widest, as with Giphy.
        status, data = await self._api("GET", address.format(key=self._keys["klipy"]), headers={},
                                       timeout=self._config.tools.fetch_timeout_seconds,
                                       params={"q": shortened(query, GIPHY_QUERY_CHARS), "per_page": str(min(50, max(8, wanted))),
                                               "page": "1", "customer_id": KLIPY_CUSTOMER, "rating": "r"})
        if status != 200:
            raise ToolFailure(f"http_{status}")
        found = []
        for item in ((data or {}).get("data") or {}).get("data") or []:
            files = item.get("file") or item.get("files") or {}
            url = next((url for size in sizes for kind in formats
                        if (url := ((files.get(size) or {}).get(kind) or {}).get("url"))), None)
            if url:
                found.append(Hit(str(item.get("title") or untitled), url))
        return found

    async def _klipy(self, query: str) -> list[Hit]:
        # The middle size uploads faster and still plays; the full one is the fallback.
        return await self._klipy_search(KLIPY, query, ("md", "hd", "sm"), ("gif",), "GIF")

    async def _klipy_memes(self, query: str) -> list[Hit]:
        return await self._klipy_search(KLIPY_MEMES, query, ("hd", "md", "sm"), ("png", "jpg", "webp"), "meme")

    async def _brave_memes(self, query: str) -> list[Hit]:
        # An image search finds memes when it is asked for one by that word.
        return await self._brave(query if "meme" in query.casefold() else f"{query} meme")

    async def _search(self, kind: str, query: str) -> tuple[str, list[Hit]]:
        """The first of this kind's services to find anything, and what it found."""
        failure: Optional[ToolFailure] = None
        answered = ""  # the last service that answered, though with nothing
        words = events.search_words(query)
        for service, method in SOURCES[kind]:
            if service not in self._keys:
                continue
            try:
                results = await getattr(self, method)(query)
            except ToolFailure as exc:
                events.log_event("image_search_failed", kind=kind, service=service, error=exc.kind, words=words)
                failure = exc
                continue
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError, AttributeError, TypeError) as exc:
                events.log_event("image_search_failed", kind=kind, service=service, error=type(exc).__name__, words=words)
                failure = ToolFailure("search_unavailable")
                continue
            if results:
                return service, results
            answered = service
        if answered:
            return answered, []  # an empty search, not a failure
        raise failure or ToolFailure("search_unavailable")

    async def _giphy(self, query: str) -> list[Hit]:
        if "giphy" not in self._keys:
            raise ToolFailure("search_unavailable")
        # No rating filter beyond the service's widest: safe search is off by the owner's decision.
        status, data = await self._api("GET", GIPHY, headers={}, timeout=self._config.tools.fetch_timeout_seconds,
                                       params={"api_key": self._keys["giphy"], "q": shortened(query, GIPHY_QUERY_CHARS),
                                               "limit": str(self._config.media.search_results),
                                               "rating": "r"})
        if status != 200:
            raise ToolFailure(f"http_{status}")
        found = []
        for item in (data or {}).get("data") or []:
            images = item.get("images") or {}
            # A smaller rendition uploads faster and still plays; the original is the fallback.
            for rendition in ("downsized_medium", "downsized", "original"):
                url = (images.get(rendition) or {}).get("url")
                if url:
                    found.append(Hit(str(item.get("title") or "GIF"), url))
                    break
        return found

    async def _brave(self, query: str) -> list[Hit]:
        if "brave" not in self._keys:
            raise ToolFailure("search_unavailable")
        month = time.strftime("%Y-%m", time.gmtime(self._clock()))
        cap = self._config.tools.search_monthly_caps.get("brave")
        if cap is not None and self._usage.count("brave", month) >= cap:
            raise ToolFailure("search_unavailable")
        self._usage.add("brave", month)
        status, data = await self._api(
            "GET", BRAVE_IMAGES, headers={"X-Subscription-Token": self._keys["brave"], "Accept": "application/json"},
            params={"q": query, "count": str(self._config.media.search_results), "safesearch": "off"},
            timeout=self._config.tools.fetch_timeout_seconds)
        if status != 200:
            raise ToolFailure(f"http_{status}")
        # Brave keeps a small still of every image it lists. It is the spare for a site that refuses the download.
        return [Hit(str(item.get("title") or "picture"), url, str((item.get("thumbnail") or {}).get("src") or ""))
                for item in (data or {}).get("results") or [] if (url := (item.get("properties") or {}).get("url"))]

    async def _download(self, hit: Hit, size_limit: int) -> Optional[tuple[bytes, str, str]]:
        """A result's image as (its bytes, what it really is, the address it came from). None if it may not be posted.

        The spare copy is tried when the image's own address refuses, is too slow or too big, or is not an image.
        """
        for url in filter(None, (hit.url, hit.spare)):
            try:
                fetched = await self._fetch(url, max_bytes=size_limit, timeout=self._config.media.download_seconds,
                                            user_agent=self._config.tools.browser_identity,
                                            max_redirects=self._config.tools.max_redirects)
                mime = media_type(fetched.data, "", self._config.files)
            except (FetchError, Unusable):
                continue  # not public, too big, or not really an image
            if mime.startswith("image/"):
                return fetched.data, mime, url
        return None

    async def find(self, kind: str, arguments: dict, context: Any) -> tuple[str, str, tuple[str, ...]]:
        """Search for images and return the ones that may be posted, with a still of each."""
        turn: Optional[MediaTurn] = context.media
        query = arguments.get("query")
        if turn is None:
            raise ToolFailure("not_this_turn")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ToolFailure("bad_request")
        if not self._config.tools.names_in_searches and names_in(query, context.names):
            raise ToolFailure("private_query")
        if turn.searches_left <= 0:
            raise ToolFailure("no_more_searches")
        turn.searches_left -= 1
        turn.searches += 1
        started = self._clock()
        service, results = await self._search(kind, query.strip())
        conn = self._store.guild(context.guild_id)
        day_ago = self._clock() - self._config.media.repeat_hours * 3600
        found: list[Candidate] = []
        repeats = 0  # images left out because they were posted here within the last day
        spares = 0  # images that came from the search service's own copy
        # Every result is fetched at once, so one slow site holds nothing up. They are taken in the
        # service's order, and whatever is still on its way when enough have passed is dropped.
        hits = results[:self._config.media.search_results]
        downloads = [asyncio.ensure_future(self._download(hit, turn.size_limit)) for hit in hits]
        try:
            for hit, download in zip(hits, downloads):
                if len(found) >= turn.per_search:
                    break
                got = await download
                if got is None:
                    continue
                data, mime, url = got
                sha = hashlib.sha256(data).hexdigest()
                # The repeat limit is for what she adds unasked: an image someone asks for may be one posted today.
                if turn.mode == "optional" and ledger.posted_since(conn, sha, day_ago):
                    repeats += 1
                    continue  # posted in the server within the last day
                if any(c.sha == sha for c in turn.candidates.values()):
                    continue  # already offered this turn
                number = len(turn.candidates) + 1
                turn.candidates[number] = Candidate(number, kind, " ".join(hit.title.split())[:80], url, data, mime, sha)
                found.append(turn.candidates[number])
                spares += url != hit.url
        finally:
            for download in downloads:
                download.cancel()
            await asyncio.gather(*downloads, return_exceptions=True)
        events.log_event("image_search", kind=kind, service=service, words=events.search_words(query),
                         results=len(results), usable=len(found), spare=spares,
                         ms=round((self._clock() - started) * 1000))
        stills = tuple((f"[image {one.number}: a still from it]" if kind == "GIF" else f"[image {one.number}]",
                        preview(one.data, self._config.media.preview_px)) for one in found)
        answer = {"success": True, "found": [{"number": one.number, "title": one.title} for one in found],
                  "next": "attach one with attach_image, with your line, if it fits, or none"}
        if not results:
            # A sentence finds nothing on these services; without this she tried another sentence.
            answer["next"] = "nothing matched those words. look again with two or three plain words, or answer without an image"
        if repeats:
            # So that she does not call another image "the same one again".
            answer["left_out"] = (f"{repeats} already posted here in the last day, which cannot be posted again today. "
                                  "the ones found are different images")
        text = json.dumps(answer, ensure_ascii=False)
        return text, f"you looked for a {kind}", stills

    def attach(self, arguments: dict, context: Any) -> tuple[str, str, str]:
        """Attach a found image. Returns the answer, the note, and her line if it came with the call."""
        turn: Optional[MediaTurn] = context.media
        if turn is None:
            raise ToolFailure("not_this_turn")
        try:
            chosen = turn.candidates[int(arguments.get("number"))]
        except (KeyError, TypeError, ValueError):
            raise ToolFailure("no_such_image") from None
        if turn.sticker is not None:
            raise ToolFailure("already_attached")  # a sticker never goes with an image
        turn.chosen = chosen  # a second choice replaces the first
        # Further images, when they asked for several: those found this turn, each once, up to the turn's limit.
        more = arguments.get("more")
        turn.extra = []
        for number in more if isinstance(more, list) else []:
            other = turn.candidates.get(number) if isinstance(number, int) and not isinstance(number, bool) else None
            if other is not None and other is not chosen and other not in turn.extra and len(turn.extra) < turn.max_images - 1:
                turn.extra.append(other)
        note = (f"you attached a {chosen.kind} to your reply" if not turn.extra
                else f"you attached {1 + len(turn.extra)} images to your reply")
        attached = chosen.number if not turn.extra else [chosen.number, *(one.number for one in turn.extra)]
        line = arguments.get("line")
        line = spoken(line) if isinstance(line, str) else ""
        if line:
            # Her words came with the image, so the reply is whole: no further model call is made.
            return json.dumps({"success": True, "attached": attached}), note, line
        return (json.dumps({"success": True, "attached": attached,
                            "next": "now write the line that goes with it: a few words, typed as you always type, "
                                    "with no full stop at the end"}), note, "")

    def sticker(self, arguments: dict, context: Any) -> tuple[str, str, str, bool]:
        """Choose one of the server's stickers for this reply.

        Returns the answer, the note, her line if it came with the call, and whether the turn ends
        here: it does when her line came with the sticker, or when she gave none and the sticker
        is her whole answer.
        """
        turn: Optional[MediaTurn] = context.media
        if turn is None or not turn.stickers:
            raise ToolFailure("not_this_turn")
        name = arguments.get("name")
        if not isinstance(name, str):
            raise ToolFailure("bad_request")
        chosen = turn.stickers.get(" ".join(name.split()).strip(":").casefold())
        if chosen is None:
            raise ToolFailure("no_such_sticker")
        if turn.chosen is not None:
            raise ToolFailure("already_attached")  # a sticker never goes with an image
        turn.sticker = chosen
        note = "you picked a sticker for your reply"
        line = arguments.get("line")
        if not isinstance(line, str) or not line.strip():
            # No words came with it: the sticker is her whole answer.
            return json.dumps({"success": True, "sticker": chosen.name}), note, "", True
        line = spoken(line)
        if line:
            return json.dumps({"success": True, "sticker": chosen.name}), note, line, True
        return (json.dumps({"success": True, "sticker": chosen.name,
                            "next": "now write the line that goes with it: a few words, typed as you always type, "
                                    "with no full stop at the end"}), note, "", False)
