"""The room log: every message in every channel she can read, kept for a week.

It is her short-term awareness of the server: the source of the room context
shown with each reply, and of the batches sent to memory.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class Line:
    message_id: str
    channel_id: str
    author_id: str
    author_name: str
    is_bot: bool
    text: str  # what was said, with any embed and sticker noted
    at: float  # seconds since the epoch
    attachments: tuple[str, ...] = ()  # filenames, in the order they were attached
    own: bool = False  # one of her own messages
    addressed: bool = False  # said to her, so it is in the channel's conversation instead
    reply_to: str = ""  # the message this one answers, if it is a reply


def record(conn: sqlite3.Connection, line: Line, *, memory: str = "pending") -> bool:
    """Add a message to the log. Returns False if it was already there.

    ``memory`` is "pending" for a message still to be offered to memory and
    "skip" for one that never will be, such as chat caught up after a restart.
    """
    cursor = conn.execute(
        "INSERT OR IGNORE INTO room_log (message_id, channel_id, author_id, author_name, is_bot, own, addressed,"
        " text, attachments, created_at, memory_state, reply_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (line.message_id, line.channel_id, line.author_id, line.author_name, int(line.is_bot), int(line.own),
         int(line.addressed), line.text, json.dumps(list(line.attachments)), line.at, memory, line.reply_to or None))
    return cursor.rowcount == 1


def _line(row: sqlite3.Row) -> Line:
    return Line(row["message_id"], row["channel_id"], row["author_id"], row["author_name"], bool(row["is_bot"]),
                row["text"], row["created_at"], tuple(json.loads(row["attachments"])), bool(row["own"]),
                bool(row["addressed"]), row["reply_to"] or "")


def overheard(conn: sqlite3.Connection, channel_id: str, since: float, limit: int) -> list[Line]:
    """The newest ``limit`` messages in a channel since ``since`` that were not hers and not said to her."""
    rows = conn.execute(
        "SELECT * FROM room_log WHERE channel_id = ? AND created_at >= ? AND own = 0 AND addressed = 0"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?", (channel_id, since, limit)).fetchall()
    return [_line(row) for row in reversed(rows)]


def newest(conn: sqlite3.Connection, since: float, *, channel_id: Optional[str] = None,
           not_channel: Optional[str] = None) -> Iterator[Line]:
    """Every logged message since ``since``, newest first: of one channel, or of every channel but one."""
    where, arguments = "created_at >= ?", [since]
    if channel_id is not None:
        where += " AND channel_id = ?"
        arguments.append(channel_id)
    if not_channel is not None:
        where += " AND channel_id != ?"
        arguments.append(not_channel)
    for row in conn.execute(f"SELECT * FROM room_log WHERE {where} ORDER BY created_at DESC, rowid DESC", arguments):
        yield _line(row)


def flagged(conn: sqlite3.Connection, message_ids: list[str]) -> set[str]:
    """Which of these messages the security check flagged. The channel's conversation keeps that mark."""
    if not message_ids:
        return set()
    marks = ", ".join("?" * len(message_ids))
    return {row[0] for row in conn.execute(
        f"SELECT message_id FROM history WHERE untrusted = 1 AND message_id IN ({marks})", message_ids)}


def channel_names(conn: sqlite3.Connection) -> dict[str, str]:
    """What each channel is called, by id."""
    return {row[0]: row[1] for row in conn.execute("SELECT channel_id, name FROM channel_names")}


def delete(conn: sqlite3.Connection, message_id: str) -> bool:
    return conn.execute("DELETE FROM room_log WHERE message_id = ?", (message_id,)).rowcount == 1


def expire(conn: sqlite3.Connection, before: float) -> int:
    """Delete log rows older than ``before``. Returns how many went."""
    return conn.execute("DELETE FROM room_log WHERE created_at < ?", (before,)).rowcount


def note_name(conn: sqlite3.Connection, author_id: str, name: str, at: float) -> None:
    """Remember that a member has gone by this name. Memory knows people by name, not by account."""
    conn.execute("INSERT INTO member_names (author_id, name, last_seen) VALUES (?, ?, ?)"
                 " ON CONFLICT (author_id, name) DO UPDATE SET last_seen = excluded.last_seen",
                 (author_id, name, at))


def note_channel(conn: sqlite3.Connection, channel_id: str, name: str, at: float) -> None:
    """Remember what a channel is called. She knows channels by id; this is for whoever reads her records."""
    conn.execute("INSERT INTO channel_names (channel_id, name, last_seen) VALUES (?, ?, ?)"
                 " ON CONFLICT (channel_id) DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen",
                 (channel_id, name, at))


def note_server(conn: sqlite3.Connection, name: str) -> None:
    """Remember what the server is called."""
    conn.execute("INSERT INTO meta (key, value) VALUES ('guild_name', ?)"
                 " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (name,))


def names_of(conn: sqlite3.Connection, author_id: str, limit: int = 5) -> tuple[str, ...]:
    """The names a member has been seen under, newest first."""
    return tuple(row[0] for row in conn.execute(
        "SELECT name FROM member_names WHERE author_id = ? ORDER BY last_seen DESC LIMIT ?", (author_id, limit)))


def accounts_named(conn: sqlite3.Connection, name: str) -> list[str]:
    """The accounts that have gone by ``name`` in this server, whatever its capitals."""
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT author_id FROM member_names WHERE name = ? COLLATE NOCASE", (name,))]


def hold_back(conn: sqlite3.Connection, author_ids: list[str], channel_id: Optional[str] = None) -> int:
    """Keep what these members have said out of memory, where it has not got there yet.

    Their own messages, and her replies to them, which repeat what was said; with ``channel_id``,
    only those in that channel. Returns how many of the members' messages were held back. What
    has already reached memory is not touched: that is forgotten as facts.
    """
    if not author_ids:
        return 0
    marks = ", ".join("?" * len(author_ids))
    where = f"memory_state = 'pending' AND own = 0 AND author_id IN ({marks})"
    arguments = list(author_ids)
    if channel_id is not None:
        where += " AND channel_id = ?"
        arguments.append(channel_id)
    held = [row[0] for row in conn.execute(f"SELECT message_id FROM room_log WHERE {where}", arguments)]
    for start in range(0, len(held), 500):
        some = held[start:start + 500]
        marks = ", ".join("?" * len(some))
        conn.execute(f"UPDATE room_log SET memory_state = 'skip' WHERE memory_state = 'pending'"
                     f" AND (message_id IN ({marks}) OR (own = 1 AND reply_to IN ({marks})))", some + some)
    return len(held)


def answers_kept_out(conn: sqlite3.Connection, message_id: str) -> bool:
    """Is this a message that is kept out of memory? Her reply to such a message is kept out too."""
    row = conn.execute("SELECT memory_state FROM room_log WHERE message_id = ?", (message_id,)).fetchone()
    return row is not None and row[0] == "skip"


def continued(conn: sqlite3.Connection, channel_id: str, at: float, within: float = 10.0) -> str:
    """What her last message in a channel was answering, if she sent it moments ago.

    A long reply goes out as two messages and only the first is a Discord reply: the second
    carries on answering the same message.
    """
    row = conn.execute("SELECT reply_to, created_at FROM room_log WHERE channel_id = ? AND own = 1"
                       " ORDER BY created_at DESC, rowid DESC LIMIT 1", (channel_id,)).fetchone()
    return row[0] if row is not None and row[0] and 0 <= at - row[1] <= within else ""


def skip_memory(conn: sqlite3.Connection, message_ids: list[str]) -> None:
    """Keep these messages out of memory: they belong to a turn the security check flagged."""
    marks = ", ".join("?" * len(message_ids))
    conn.execute(f"UPDATE room_log SET memory_state = 'skip' WHERE memory_state = 'pending' AND message_id IN ({marks})",
                 message_ids)
