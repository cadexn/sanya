"""What her per-server databases hold, read and never written.

Every function takes a connection opened by ``files.open_database``, which cannot write. The
words of the chat are here and nowhere else: the event log has none.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

PAGE = 100
_ROOM = ("message_id, channel_id, author_id, author_name, is_bot, own, addressed, text, attachments, created_at,"
         " memory_state, memory_doc, reply_to")


def guild_ids(data_dir: Path) -> list[str]:
    """The servers that have a database."""
    try:
        return sorted(path.stem for path in (Path(data_dir) / "guilds").glob("*.db") if path.stem.isdigit())
    except OSError:
        return []


def database(data_dir: Path, guild_id: str) -> Path:
    if not guild_id.isdigit():
        raise ValueError("a server id is a string of digits")
    return Path(data_dir) / "guilds" / f"{guild_id}.db"


def _marks(values: list) -> str:
    return ", ".join("?" * len(values))


def _message(row: sqlite3.Row) -> dict:
    try:
        attachments = json.loads(row["attachments"])
    except (TypeError, ValueError):
        attachments = []
    return {"id": row["message_id"], "channel": row["channel_id"], "author": row["author_id"],
            "name": row["author_name"], "bot": bool(row["is_bot"]), "own": bool(row["own"]),
            "addressed": bool(row["addressed"]), "text": row["text"], "attachments": attachments,
            "at": row["created_at"], "memory": row["memory_state"], "document": row["memory_doc"],
            "reply_to": row["reply_to"] or None}


def _has(conn: sqlite3.Connection, table: str) -> bool:
    """Does the database have this table yet? Some arrive only when she is next started on newer code."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def channel_names(conn: sqlite3.Connection) -> dict[str, str]:
    """The channels' names, as she has recorded them since she began to."""
    if not _has(conn, "channel_names"):
        return {}
    return {row[0]: row[1] for row in conn.execute("SELECT channel_id, name FROM channel_names")}


def server_name(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = 'guild_name'").fetchone()
    return row[0] if row else ""


def recalled(conn: sqlite3.Connection, message_id: str) -> Optional[list[dict]]:
    """The facts she had from memory for the reply to a message. None if her records do not hold these yet."""
    if not _has(conn, "recalled"):
        return None
    return [{"source": row[0], "fact": row[1], "text": row[2]} for row in conn.execute(
        "SELECT source, fact_id, text FROM recalled WHERE message_id = ? ORDER BY id", (message_id,))]


def her_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT author_id FROM room_log WHERE own = 1 LIMIT 1").fetchone()
    return row[0] if row else ""


def channels(conn: sqlite3.Connection) -> list[dict]:
    """Every channel with something in the room log or the conversation, the most recently active first."""
    found: dict[str, dict] = {}
    for row in conn.execute(
            "SELECT channel_id, COUNT(*) AS messages, MAX(created_at) AS last, SUM(memory_state = 'pending') AS waiting,"
            " SUM(addressed) AS addressed, SUM(own) AS own FROM room_log GROUP BY channel_id"):
        found[row["channel_id"]] = {"id": row["channel_id"], "messages": row["messages"], "last": row["last"],
                                    "waiting": row["waiting"] or 0, "addressed": row["addressed"] or 0,
                                    "own": row["own"] or 0, "told": 0}
    for row in conn.execute("SELECT channel_id, COUNT(*) AS told, MAX(created_at) AS last FROM history"
                            " GROUP BY channel_id"):
        entry = found.setdefault(row["channel_id"], {"id": row["channel_id"], "messages": 0, "last": row["last"],
                                                     "waiting": 0, "addressed": 0, "own": 0, "told": 0})
        entry["told"] = row["told"]
        entry["last"] = max(entry["last"] or 0, row["last"] or 0)
    return sorted(found.values(), key=lambda entry: entry["last"] or 0, reverse=True)


def room(conn: sqlite3.Connection, channel_id: str, *, before: Optional[float] = None, limit: int = PAGE,
         only: str = "", q: str = "") -> list[dict]:
    """A channel's room log, newest first: everything she heard there."""
    where, arguments = ["channel_id = ?"], [channel_id]
    if before is not None:
        where.append("created_at < ?")
        arguments.append(before)
    if only == "addressed":
        where.append("addressed = 1")
    elif only == "own":
        where.append("own = 1")
    elif only in ("pending", "done", "skip"):
        where.append("memory_state = ?")
        arguments.append(only)
    if q:
        where.append("text LIKE ? ESCAPE '\\'")
        arguments.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    rows = conn.execute(f"SELECT {_ROOM} FROM room_log WHERE {' AND '.join(where)}"
                        " ORDER BY created_at DESC, rowid DESC LIMIT ?", [*arguments, limit]).fetchall()
    return [_message(row) for row in rows]


def messages(conn: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, dict]:
    """Room-log rows by message id."""
    ids = sorted({message_id for message_id in message_ids if message_id})
    found: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        some = ids[start:start + 500]
        for row in conn.execute(f"SELECT {_ROOM} FROM room_log WHERE message_id IN ({_marks(some)})", some):
            found[row["message_id"]] = _message(row)
    return found


def told(conn: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, dict]:
    """What the conversation holds for these messages: it is kept longer than the room log."""
    ids = sorted({message_id for message_id in message_ids if message_id})
    found: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        some = ids[start:start + 500]
        for row in conn.execute("SELECT message_id, channel_id, author_id, author_name, text, created_at, untrusted, notes"
                                f" FROM history WHERE role = 'user' AND message_id IN ({_marks(some)})", some):
            found[row["message_id"]] = {"id": row["message_id"], "channel": row["channel_id"], "author": row["author_id"],
                                        "name": row["author_name"], "text": row["text"], "at": row["created_at"],
                                        "untrusted": bool(row["untrusted"]), "notes": row["notes"]}
    return found


def replies(conn: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, list[dict]]:
    """Her messages that answer these, by the message answered, oldest first."""
    ids = sorted({message_id for message_id in message_ids if message_id})
    found: dict[str, list[dict]] = {}
    for start in range(0, len(ids), 500):
        some = ids[start:start + 500]
        for row in conn.execute(f"SELECT {_ROOM} FROM room_log WHERE own = 1 AND reply_to IN ({_marks(some)})"
                                " ORDER BY created_at, rowid", some):
            found.setdefault(row["reply_to"], []).append(_message(row))
    return found


def own_between(conn: sqlite3.Connection, channel_id: str, start: float, end: float) -> list[dict]:
    """What she posted in a channel between two moments: a turn's reply, when it was not sent as a Discord reply."""
    rows = conn.execute(f"SELECT {_ROOM} FROM room_log WHERE own = 1 AND channel_id = ? AND created_at BETWEEN ? AND ?"
                        " ORDER BY created_at, rowid", (channel_id, start, end)).fetchall()
    return [_message(row) for row in rows]


def asked(conn: sqlite3.Connection, channel_id: str, author_id: str, upto: float, count: int) -> list[dict]:
    """The last ``count`` messages a member said to her in a channel up to a moment, oldest first."""
    rows = conn.execute(f"SELECT {_ROOM} FROM room_log WHERE channel_id = ? AND author_id = ? AND addressed = 1"
                        " AND own = 0 AND created_at <= ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                        (channel_id, author_id, upto, count)).fetchall()
    return [_message(row) for row in reversed(rows)]


def history(conn: sqlite3.Connection, channel_id: str, *, before: Optional[int] = None, limit: int = PAGE) -> list[dict]:
    """A channel's conversation as she is given it, newest first: what was said to her and what she answered."""
    where, arguments = "channel_id = ?", [channel_id]
    if before is not None:
        where += " AND id < ?"
        arguments.append(before)
    rows = conn.execute("SELECT id, role, author_id, author_name, message_id, text, created_at, untrusted, notes"
                        f" FROM history WHERE {where} ORDER BY id DESC LIMIT ?", [*arguments, limit]).fetchall()
    return [{"row": row["id"], "role": row["role"], "author": row["author_id"], "name": row["author_name"],
             "id": row["message_id"], "text": row["text"], "at": row["created_at"],
             "untrusted": bool(row["untrusted"]), "notes": row["notes"]} for row in rows]


def stored_files(conn: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, list[dict]]:
    """The files kept for these messages, by message, in the order they were attached."""
    ids = sorted({message_id for message_id in message_ids if message_id})
    found: dict[str, list[dict]] = {}
    for start in range(0, len(ids), 500):
        some = ids[start:start + 500]
        for row in conn.execute("SELECT id, message_id, position, kind, filename, mime, size, description, shown_at,"
                                f" created_at FROM files WHERE message_id IN ({_marks(some)}) ORDER BY position", some):
            found.setdefault(row["message_id"], []).append(_file(row))
    return found


def _file(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "message": row["message_id"], "position": row["position"], "kind": row["kind"],
            "name": row["filename"], "mime": row["mime"], "size": row["size"], "description": row["description"],
            "shown_at": row["shown_at"], "at": row["created_at"]}


def stored_file(conn: sqlite3.Connection, file_id: int) -> Optional[dict]:
    row = conn.execute("SELECT id, message_id, position, kind, filename, mime, size, description, shown_at, created_at,"
                       " path FROM files WHERE id = ?", (file_id,)).fetchone()
    return {**_file(row), "path": row["path"]} if row else None


def batches(conn: sqlite3.Connection, limit: int = PAGE) -> list[dict]:
    """The batches of chat sent to memory, newest first."""
    rows = conn.execute("SELECT document_id, channel_id, messages, created_at, curated FROM memory_batches"
                        " ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_batch(row) for row in rows]


def _batch(row: sqlite3.Row) -> dict:
    return {"document": row["document_id"], "channel": row["channel_id"], "messages": row["messages"],
            "at": row["created_at"], "curated": bool(row["curated"])}


def batch(conn: sqlite3.Connection, document_id: str) -> Optional[dict]:
    row = conn.execute("SELECT document_id, channel_id, messages, created_at, curated FROM memory_batches"
                       " WHERE document_id = ?", (document_id,)).fetchone()
    return _batch(row) if row else None


def batch_messages(conn: sqlite3.Connection, document_id: str) -> list[dict]:
    """The messages of a batch that are still in the room log, oldest first."""
    rows = conn.execute(f"SELECT {_ROOM} FROM room_log WHERE memory_doc = ? ORDER BY created_at, rowid",
                        (document_id,)).fetchall()
    return [_message(row) for row in rows]


def waiting(conn: sqlite3.Connection) -> list[dict]:
    """Per channel, the messages still to be offered to memory."""
    rows = conn.execute("SELECT channel_id, COUNT(*) AS waiting, MIN(created_at) AS oldest, MAX(created_at) AS newest"
                        " FROM room_log WHERE memory_state = 'pending' GROUP BY channel_id ORDER BY oldest").fetchall()
    return [{"channel": row["channel_id"], "waiting": row["waiting"], "oldest": row["oldest"], "newest": row["newest"]}
            for row in rows]


def removals(conn: sqlite3.Connection) -> list[str]:
    """Batches waiting to be taken back, because a message of theirs was deleted."""
    return [row[0] for row in conn.execute("SELECT document_id FROM memory_removals")]


def names(conn: sqlite3.Connection) -> dict[str, str]:
    """Each member's most recent name."""
    found: dict[str, str] = {}
    for row in conn.execute("SELECT author_id, name FROM member_names ORDER BY last_seen"):
        found[row["author_id"]] = row["name"]
    return found


def counts(conn: sqlite3.Connection) -> dict[str, Any]:
    """How much the database holds, for the health view."""
    out: dict[str, Any] = {}
    for table in ("room_log", "history", "files", "memory_batches", "member_names", "media_ledger"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    states = dict(conn.execute("SELECT memory_state, COUNT(*) FROM room_log GROUP BY memory_state").fetchall())
    out["memory"] = {state: states.get(state, 0) for state in ("pending", "done", "skip")}
    out["undescribed"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE description IS NULL AND mime LIKE 'image/%'").fetchone()[0]
    out["schema"] = conn.execute("PRAGMA user_version").fetchone()[0]
    return out
