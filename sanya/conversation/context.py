"""Room context: what was said around her lately in the channel she is replying in.

It comes from the room log, never from a fresh fetch. It covers only this
channel; other channels reach her through memory.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..intake.attachments import FileStore
from ..store import room_log
from .prompt import captioned, speaker_name

HEADING = "[Earlier in this channel, not said to you]"
CLOSING = "[Said to you now]"


@dataclass(frozen=True)
class RoomContext:
    text: str = ""  # the block put before the messages addressed to her; empty if nothing was overheard
    pictures: tuple = ()  # the newest overheard pictures, each a (caption, link) pair
    names: tuple[str, ...] = ()  # who spoke, for the search privacy check
    lines: int = 0
    shown: tuple[int, ...] = ()  # the files shown this time, to be marked once her reply is out


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build(conn: sqlite3.Connection, files: FileStore, guild_id: str, channel_id: str, *, now: float,
          minutes: int, max_messages: int, pictures: int, line_chars: int) -> RoomContext:
    lines = room_log.overheard(conn, channel_id, now - minutes * 60, max_messages)
    if not lines:
        return RoomContext()
    stored = files.for_messages(guild_id, [line.message_id for line in lines])
    # The newest few pictures she has not yet seen are shown to her, once. The rest she can open by number.
    shown: list[tuple[int, list[tuple[str, str]]]] = []
    for line in reversed(lines):
        for position in sorted(stored.get(line.message_id, {}), reverse=True):
            one = stored[line.message_id][position]
            if one.is_image and not one.seen and len(shown) < pictures:
                try:
                    who = speaker_name(line.author_name)
                    caption = f"[file {one.file_id}: a picture {who} posted earlier, not to you]"
                    shown.append((one.file_id, captioned(caption, files.pictures(one))))
                except OSError:
                    pass  # the owner has cleared the file away
    shown_ids = {file_id for file_id, _ in shown}
    rendered = [HEADING]
    for line in lines:
        notes = []
        for position, filename in enumerate(line.attachments):
            one = stored.get(line.message_id, {}).get(position)
            label = _cut(filename.replace("[", " ").replace("]", " "), 60)
            if one is None:
                notes.append(label)
            else:
                state = ", shown" if one.file_id in shown_ids else (", seen earlier" if one.seen else "")
                notes.append(f"{label} (file {one.file_id}{state})")
        said = _cut(line.text, line_chars)
        if notes:
            said = f"{said} [attached: {', '.join(notes)}]".strip()
        # Everyone is just someone to her: whether an account is a bot is not shown.
        who = speaker_name(line.author_name)
        rendered.append(f"[{datetime.fromtimestamp(line.at):%H:%M}] {who}: {said}")
    rendered.append(CLOSING)
    return RoomContext("\n".join(rendered),
                       tuple(pair for _, pairs in reversed(shown) for pair in pairs),
                       tuple({line.author_name for line in lines if line.author_name}), len(lines),
                       tuple(sorted(shown_ids)))
