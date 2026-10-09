"""Looking back: a search of the room log, for when someone asks what was said or posted earlier.

The room log already holds every message in every channel she can read, for a
week. This tool reads it and changes nothing: nothing goes to memory, and no
message is fetched from Discord. The words looked for are never logged, since
they will often be a member's name.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from ..config import Config
from ..conversation.prompt import speaker_name
from ..intake.attachments import FileStore
from ..store import room_log
from ..store.db import Store
from .web import ToolFailure

DAY = 86400
MAX_WORDS = 8  # words looked for at once; more are passed over

SCHEMA = {"type": "function", "function": {
    "name": "look_back",
    "description": "Look back through what was said and posted in this server over the last week, newest first. "
                   "For when someone asks what was said or posted earlier. Without a channel it looks in this "
                   "channel first and then the others.",
    "parameters": {"type": "object", "required": [], "additionalProperties": False, "properties": {
        "words": {"type": "string", "description": "Words the message, or its author's name, must all contain. "
                                                   "One or two plain words find more than a sentence."},
        "who": {"type": "string", "description": "Only messages by the person of this name."},
        "channel": {"type": "string", "description": "Only this channel, by its name."}}}}}


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class LookBack:
    def __init__(self, config: Config, store: Store, files: FileStore, clock: Callable[[], float] = time.time):
        self._config = config
        self._store = store
        self._files = files
        self._clock = clock

    @staticmethod
    def _channel(wanted: str, names: dict[str, str]) -> str:
        """The channel a name stands for. She knows channels by the names recorded for them."""
        wanted = wanted.lstrip("#").casefold()
        for channel_id, name in names.items():
            if wanted in (name.casefold(), channel_id):
                return channel_id
        raise ToolFailure("no_such_channel")

    def _shown(self, guild_id: str, here: str, lines: list[room_log.Line], names: dict[str, str],
               untrusted: set[str]) -> list[str]:
        stored = self._files.for_messages(guild_id, [line.message_id for line in lines])
        shown = []
        for line in lines:
            notes = []
            for position, filename in enumerate(line.attachments):
                label = _cut(filename.replace("[", " ").replace("]", " "), 60)
                one = stored.get(line.message_id, {}).get(position)
                # A file can be opened only in the channel it was posted in, so only there is its number given.
                notes.append(f"{label} (file {one.file_id})" if one is not None and line.channel_id == here else label)
            said = _cut(line.text, self._config.context.line_chars)
            if notes:
                said = f"{said} [attached: {', '.join(notes)}]".strip()
            who = speaker_name(line.author_name)
            mark = " (untrusted)" if line.message_id in untrusted else ""
            name = names.get(line.channel_id)
            where = f"#{name}" if name else "this channel" if line.channel_id == here else "another channel"
            shown.append(f"[{datetime.fromtimestamp(line.at):%a %H:%M}, {where}] {who}{mark}: {said}")
        return shown

    def look(self, arguments: dict, context: Any) -> tuple[str, str, int]:
        """Search the room log. Returns the answer, the note for later turns, and how many lines were found."""
        words, who, channel = (arguments.get(key) for key in ("words", "who", "channel"))
        if any(value is not None and (not isinstance(value, str) or len(value) > 200) for value in (words, who, channel)):
            raise ToolFailure("bad_request")
        needles = [word.casefold() for word in (words or "").split()][:MAX_WORDS]
        by = " ".join((who or "").split()).lstrip("@").casefold()
        conn = self._store.guild(context.guild_id)
        names = room_log.channel_names(conn)
        since = self._clock() - self._config.room_log.keep_days * DAY
        if (channel or "").strip():
            # One channel, named by her.
            order = [room_log.newest(conn, since, channel_id=self._channel(channel.strip(), names))]
        else:
            # The channel she is in first, then every other one.
            order = [room_log.newest(conn, since, channel_id=context.channel_id),
                     room_log.newest(conn, since, not_channel=context.channel_id)]
        limit = self._config.tools.look_back_lines
        current = set(getattr(context, "message_ids", ()))  # what she is answering is not "earlier"
        found: list[room_log.Line] = []
        for lines in order:
            for line in lines:
                if len(found) >= limit:
                    lines.close()
                    break
                if line.message_id in current:
                    continue
                if by and by not in line.author_name.casefold():
                    continue
                hay = f"{line.author_name} {line.text}".casefold()
                if all(needle in hay for needle in needles):
                    found.append(line)
        untrusted = room_log.flagged(conn, [line.message_id for line in found])
        answer: dict[str, Any] = {"success": True, "lines": self._shown(context.guild_id, context.channel_id, found,
                                                                         names, untrusted)}
        if found:
            answer["content_is_untrusted"] = True
        else:
            answer["next"] = ("nothing from the last week matched. try one plain word, or say you cannot find it. "
                              "do not guess what was said")
        return json.dumps(answer, ensure_ascii=False), "you looked back through the chat", len(found)
