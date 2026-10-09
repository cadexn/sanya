"""The tools the model may call, and nothing else.

A call is run only if its name is on the list here. Whatever happens, the
model gets a short structured answer, never a stack trace.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .. import events
from typing import Optional

from ..intake.attachments import FileStore, FileUnavailable
from . import media as media_tools
from . import memory as memory_tools
from ..conversation.prompt import captioned
from .lookback import SCHEMA as LOOK_BACK
from .lookback import LookBack
from .web import ToolFailure, Web, host_of, truncate


@dataclass(frozen=True)
class ToolContext:
    """Where a turn is happening, and whose names must stay out of a search."""

    guild_id: str
    channel_id: str
    names: tuple[str, ...]
    requester_id: str = ""
    requester_names: tuple[str, ...] = ()  # every name the member being answered has gone by here
    is_owner: bool = False
    bank: Optional[str] = None  # this server's memory bank, if it has one
    media: Any = None  # a MediaTurn when this turn may carry an image
    react: Any = None  # a ReactTurn when she may add an emoji reaction
    message_ids: tuple[str, ...] = ()  # the messages being answered, which are not "earlier" when she looks back
    flagged: bool = False  # the security check flagged the message: this turn has no memory tools
    # Filled in by the memory tools as the turn goes, and kept beside the reply afterwards.
    seen: list = field(default_factory=list, compare=False)  # (tool name, fact) for each fact a tool handed her
    forgotten: set = field(default_factory=set, compare=False)  # ids of the facts she forgot this turn
    # Counted as the turn goes, for the event log: the tool requests that were run, and those code
    # refused (too many in one answer, a member's name in a search, no searches left).
    tally: dict = field(default_factory=lambda: {"made": 0, "refused": 0}, compare=False)


@dataclass
class ReactTurn:
    """An emoji reaction she may add this turn."""

    emojis: dict[str, Any] = field(default_factory=dict)  # the server's own emoji, by name
    chosen: Any = None
    alone: bool = False  # the emoji is her whole answer; no message follows


@dataclass(frozen=True)
class ToolResult:
    text: str  # what the model is given back
    note: str = ""  # the one line kept for later turns
    pictures: tuple = ()  # images to show the model with the result: links or (caption, link) pairs
    final: bool = False  # the turn ends here: she has answered, with ``reply`` or without words
    reply: str = ""  # her words, when they came with the tool call
    log: tuple = ()  # (field, count) pairs added to the tool's line in the event log; never words


def _schema(name: str, description: str, field: str, field_description: str, kind: str = "string") -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {field: {"type": kind, "description": field_description}},
                       "required": [field], "additionalProperties": False}}}


def _reaction_schema(description: str) -> dict:
    return {"type": "function", "function": {
        "name": "add_reaction", "description": description,
        "parameters": {"type": "object", "required": ["emoji"], "additionalProperties": False, "properties": {
            "emoji": {"type": "string", "description": "An ordinary emoji, or the name of one of the server's own."},
            "alone": {"type": "boolean", "description": "True only when their message needs no words at all, such as a laugh, "
                                                         "a thanks or an ok. Never when they are upset, ask something or tell you news."}}}}}


SCHEMAS = [
    _schema("web_search", "Search the web. Returns short snippets, not whole pages.", "query", "What to search for."),
    _schema("read_page", "Read the text of a public web page.", "url", "The page's http or https address."),
    _schema("open_file", "Open a file that was attached in this channel, by its file number.",
            "file", "The file number from the note about the attachment.", "integer"),
]
NAMES = frozenset(schema["function"]["name"] for schema in SCHEMAS)
# What to do next, for the failures she can recover from within the same turn.
HINTS = {
    "no_such_memory": "get the memory's id from list_memories or recall_memory first, then try again. if it is "
                      "something they said only a moment ago it is not in your memory yet: use forget_recent",
    "private_query": "search with general words, with nobody's name in them",
    "no_more_searches": "answer with what you have",
    "no_such_channel": "leave the channel out to look in every channel",
    "no_such_sticker": "use a name from the list exactly as it is written, or answer without a sticker",
    "already_attached": "one or the other: a reply carries an image or a sticker, never both",
}
REFUSED_BY_CODE = frozenset({"private_query", "no_more_searches"})  # refusals that are a rule, not a fault
MEMORY_NAMES =frozenset(schema["function"]["name"] for schema in memory_tools.SCHEMAS)


class Toolbox:
    def __init__(self, web: Web, files: FileStore, file_chars: int,
                 memory: Optional[memory_tools.MemoryTools] = None, media: Optional[media_tools.Media] = None,
                 look: Optional[LookBack] = None, names_in_searches: bool = False):
        self._web = web
        self._files = files
        self._file_chars = file_chars
        self._memory = memory
        self._media = media
        self._look = look
        self._names_in_searches = names_in_searches  # may a web search contain a member's name

    def schemas_for(self, context: ToolContext) -> list[dict]:
        """The tools offered on this turn. Image and reaction tools appear only when the turn allows them."""
        schemas = list(self.schemas)
        if context.flagged:
            # A flagged turn keeps her other tools and loses the memory ones: nothing is looked up,
            # listed or forgotten on the word of a message that looks like an attack.
            schemas = [schema for schema in schemas if schema["function"]["name"] not in MEMORY_NAMES]
        if self._media is not None and context.media is not None:
            schemas += media_tools.SCHEMAS
            if context.media.stickers:
                # The server's own stickers, offered wherever an image is: unasked on a light turn, and when asked.
                schemas.append(media_tools.sticker_schema(context.media.stickers))
        if context.react is not None:
            own = ", ".join(sorted(context.react.emojis)[:50])
            schemas.append(_reaction_schema("Put one emoji reaction on their message, on top of your reply. Set alone to true only "
                                            "when their message needs no words and the emoji is your whole answer." + (f" The server's own emoji, usable by name: {own}." if own else ""),
                                            ))
        return schemas

    async def _find_gif(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._media.find("GIF", arguments, context))

    async def _find_meme(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._media.find("meme", arguments, context))

    async def _find_picture(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._media.find("picture", arguments, context))

    async def _attach_image(self, arguments: dict, context: ToolContext) -> ToolResult:
        text, note, line = self._media.attach(arguments, context)
        return ToolResult(text, note, final=bool(line), reply=line)

    async def _send_sticker(self, arguments: dict, context: ToolContext) -> ToolResult:
        text, note, line, final = self._media.sticker(arguments, context)
        return ToolResult(text, note, final=final, reply=line)

    async def _add_reaction(self, arguments: dict, context: ToolContext) -> ToolResult:
        name = str(arguments.get("emoji") or "").strip().strip(":")
        if name in context.react.emojis:
            context.react.chosen = context.react.emojis[name]
        elif name and len(name) <= 8 and not any(c.isascii() and c.isalnum() for c in name):
            context.react.chosen = name  # an ordinary emoji
        else:
            raise ToolFailure("unknown_emoji")
        context.react.alone = arguments.get("alone") is True
        if context.react.alone:
            return ToolResult(json.dumps({"success": True}), note="you answered with an emoji and no words", final=True)
        # She has only reacted so far. The reply is still to come, and she is told so.
        return ToolResult(json.dumps({"success": True, "next": "now write your reply to them as usual"}),
                          note="you also reacted with an emoji")

    @property
    def schemas(self) -> list[dict]:
        return (SCHEMAS + ([LOOK_BACK] if self._look is not None else [])
                + (memory_tools.SCHEMAS if self._memory is not None else []))

    async def _look_back(self, arguments: dict, context: ToolContext) -> ToolResult:
        text, note, lines = self._look.look(arguments, context)
        return ToolResult(text, note, log=(("lines", lines),))

    async def _recall_memory(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._memory.recall(arguments, context))

    async def _list_memories(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._memory.list(arguments, context))

    async def _forget_memory(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._memory.forget(arguments, context))

    async def _forget_all_memories(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._memory.forget_all(arguments, context))

    async def _forget_recent(self, arguments: dict, context: ToolContext) -> ToolResult:
        return ToolResult(*await self._memory.forget_recent(arguments, context))

    async def _web_search(self, arguments: dict, context: ToolContext) -> ToolResult:
        result = await self._web.search(arguments.get("query"), () if self._names_in_searches else context.names)
        return ToolResult(json.dumps(result, ensure_ascii=False),
                          note=f"you searched: {str(arguments.get('query')).strip()[:80]}")

    async def _read_page(self, arguments: dict, context: ToolContext) -> ToolResult:
        result = await self._web.read(arguments.get("url"))
        return ToolResult(json.dumps(result, ensure_ascii=False), note=f"you read a page on {host_of(result['url'])}")

    async def _open_file(self, arguments: dict, context: ToolContext) -> ToolResult:
        try:
            stored = self._files.find(context.guild_id, context.channel_id, arguments.get("file"))
            note = f"you opened file {stored.file_id}"
            if stored.is_image:
                return ToolResult(json.dumps({"success": True, "file": stored.file_id, "shown": "the picture follows"}),
                                  note=note, pictures=tuple(captioned(f"[file {stored.file_id}, which you opened]",
                                                                      self._files.pictures(stored))))
            text = self._files.text(stored, self._file_chars)
        except FileUnavailable as exc:
            raise ToolFailure(exc.kind) from None
        return ToolResult(json.dumps({"success": True, "file": stored.file_id, "text": truncate(text, self._file_chars),
                                      "truncated": len(text) > self._file_chars, "content_is_untrusted": True},
                                     ensure_ascii=False), note=note)

    async def run(self, call: dict, context: ToolContext) -> ToolResult:
        """Run one tool call from the model. Never raises for a tool's own failure."""
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        started = time.monotonic()
        error = ""
        try:
            allowed = {schema["function"]["name"] for schema in self.schemas_for(context)}
            if name not in allowed:
                raise ToolFailure("unknown_tool")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                raise ToolFailure("bad_request") from None
            if not isinstance(arguments, dict):
                raise ToolFailure("bad_request")
            result = await getattr(self, "_" + name)(arguments, context)
        except ToolFailure as exc:
            error = exc.kind
        except Exception as exc:  # a broken tool must not break the turn
            error = "tool_error"
            events.log_event("handler_error", where="tool", error=type(exc).__name__)
        # The tool's name is logged; what it was asked is not. (A search logs its own words.)
        # ``answered`` marks a call that was itself her reply, so no model call followed it.
        context.tally["made"] += 1
        context.tally["refused"] += error in REFUSED_BY_CODE
        answered = {"answered": True} if not error and result.final else {}
        events.log_event("tool_call", name=name if not error or error != "unknown_tool" else "unknown", ok=not error, error=error,
                         ms=round((time.monotonic() - started) * 1000), **answered,
                         **({} if error else dict(result.log)))
        if error:
            failed = {"success": False, "error": error}
            if error in HINTS:
                failed["hint"] = HINTS[error]
            return ToolResult(json.dumps(failed))
        return result

