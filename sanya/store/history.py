"""Channel conversations: what was said to her and what she answered.

One conversation per channel, shared by everyone in it. Rows are deleted on a
rolling limit set in the config (30 days).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Entry:
    channel_id: str
    role: str  # "user" for a member's message, "assistant" for hers
    text: str
    at: float  # seconds since the epoch
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    message_id: Optional[str] = None
    untrusted: bool = False  # flagged by the security check
    notes: Optional[str] = None  # one line per tool she used answering it


def append_many(conn: sqlite3.Connection, entries: Iterable[Entry]) -> None:
    """Save one turn's entries together, or none of them."""
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT INTO history (channel_id, role, author_id, author_name, message_id, text, created_at, untrusted,"
            " notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(e.channel_id, e.role, e.author_id, e.author_name, e.message_id, e.text, e.at, int(e.untrusted), e.notes)
             for e in entries])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def recent(conn: sqlite3.Connection, channel_id: str, limit: int) -> list[Entry]:
    """The last ``limit`` entries of a channel's conversation, oldest first."""
    rows = conn.execute(
        "SELECT channel_id, role, text, created_at, author_id, author_name, message_id, untrusted, notes FROM history"
        " WHERE channel_id = ? ORDER BY id DESC LIMIT ?", (channel_id, limit)).fetchall()
    return [Entry(*row[:7], bool(row[7]), row[8]) for row in reversed(rows)]


def expire(conn: sqlite3.Connection, before: float) -> int:
    """Delete entries older than ``before``. Returns how many went."""
    return conn.execute("DELETE FROM history WHERE created_at < ?", (before,)).rowcount


def delete_message(conn: sqlite3.Connection, message_id: str) -> int:
    """Remove a member's message from the conversation, when they have deleted it in Discord."""
    return conn.execute("DELETE FROM history WHERE message_id = ? AND role = 'user'", (message_id,)).rowcount
