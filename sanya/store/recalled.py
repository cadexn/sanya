"""What she had from memory for each reply: a copy of the facts, kept beside the turn.

She never reads this. It is for whoever looks at her records afterwards and asks why she said
what she said. By the owner's decision it is a copy of each fact's text, kept as long as the
conversation, and a copy never outlives its fact: when a fact leaves memory, because a member
had her forget it, a message was deleted or hearsay expired, its copies go too.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Iterable, Optional, TypeVar

from .. import events

RECALL = "recall"  # the facts put into her prompt before she answers; any other source is a memory tool's name
T = TypeVar("T")


def safely(where: str, action: Callable[[], T]) -> Optional[T]:
    """Do a piece of this record-keeping so that it can never cost her a reply or a message.

    What is kept here, like the names of channels, is for whoever reads her records later. If the
    database will not take it, that is logged and she carries on.
    """
    try:
        return action()
    except sqlite3.Error as exc:
        events.log_event("handler_error", where=where, error=type(exc).__name__)
        return None


def save(conn: sqlite3.Connection, message_id: str, entries: Iterable[tuple[str, str, str]], at: float) -> int:
    """Keep the facts of one turn. Each entry is (source, fact id, fact text). Returns how many were kept.

    A fact handed to her twice in one turn by the same source is kept once.
    """
    seen: set[tuple[str, str]] = set()
    rows = []
    for source, fact_id, text in entries:
        if fact_id and (source, fact_id) not in seen:
            seen.add((source, fact_id))
            rows.append((message_id, source, fact_id, " ".join(text.split()), at))
    conn.executemany("INSERT INTO recalled (message_id, source, fact_id, text, created_at) VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def for_message(conn: sqlite3.Connection, message_id: str) -> list[tuple[str, str, str]]:
    """The facts kept for the turn that answered ``message_id``, in the order she was given them."""
    return [tuple(row) for row in conn.execute(
        "SELECT source, fact_id, text FROM recalled WHERE message_id = ? ORDER BY id", (message_id,))]


def forget_facts(conn: sqlite3.Connection, fact_ids: Iterable[str]) -> int:
    """Remove every copy of these facts. Returns how many copies went."""
    ids = sorted({fact_id for fact_id in fact_ids if fact_id})
    gone = 0
    for start in range(0, len(ids), 500):
        some = ids[start:start + 500]
        marks = ", ".join("?" * len(some))
        gone += conn.execute(f"DELETE FROM recalled WHERE fact_id IN ({marks})", some).rowcount
    return gone


def delete_message(conn: sqlite3.Connection, message_id: str) -> int:
    """Remove what was kept for a turn, when the message it answered is deleted in Discord."""
    return conn.execute("DELETE FROM recalled WHERE message_id = ?", (message_id,)).rowcount


def expire(conn: sqlite3.Connection, before: float) -> int:
    """Delete copies older than ``before``. Returns how many went."""
    return conn.execute("DELETE FROM recalled WHERE created_at < ?", (before,)).rowcount
