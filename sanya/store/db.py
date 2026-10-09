"""SQLite storage: one database file per server.

Each server gets its own file, so nothing stored for one can surface in
another. Schema changes are numbered migrations applied in order; the file
records the last one applied in ``PRAGMA user_version``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# One entry per schema version. Each entry is the statements that move a
# database from the version before it. Never edit an entry that has shipped;
# add a new one.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    # 1: bookkeeping only. Tables arrive with the stages that use them.
    ("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",),
    # 2: the channel conversations (stage 2).
    ("""CREATE TABLE history (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            author_id TEXT,
            author_name TEXT,
            message_id TEXT,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        )""",
     "CREATE INDEX history_by_channel ON history (channel_id, id)",
     "CREATE INDEX history_by_age ON history (created_at)"),
    # 3: the security check's mark on a message (stage 3).
    ("ALTER TABLE history ADD COLUMN untrusted INTEGER NOT NULL DEFAULT 0",),
    # 4: stored files, and the one-line notes about tools she used (stage 4).
    ("""CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            author_id TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('sent', 'overheard')),
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            path TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE (message_id, position)
        )""",
     "ALTER TABLE history ADD COLUMN notes TEXT"),
    # 5: the room log (stage 5).
    ("""CREATE TABLE room_log (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            is_bot INTEGER NOT NULL,
            own INTEGER NOT NULL,
            addressed INTEGER NOT NULL,
            text TEXT NOT NULL,
            attachments TEXT NOT NULL,
            created_at REAL NOT NULL,
            memory_state TEXT NOT NULL CHECK (memory_state IN ('pending', 'skip', 'done'))
        )""",
     "CREATE INDEX room_log_by_channel ON room_log (channel_id, created_at)",
     "CREATE INDEX room_log_by_age ON room_log (created_at)",
     "CREATE INDEX history_by_message ON history (message_id)"),
    # 6: memory (stage 6).
    ("ALTER TABLE room_log ADD COLUMN memory_doc TEXT",
     "ALTER TABLE files ADD COLUMN description TEXT",
     "CREATE INDEX room_log_by_memory ON room_log (memory_state, channel_id, created_at)",
     """CREATE TABLE member_names (
            author_id TEXT NOT NULL,
            name TEXT NOT NULL,
            last_seen REAL NOT NULL,
            PRIMARY KEY (author_id, name)
        )""",
     """CREATE TABLE memory_batches (
            document_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            messages INTEGER NOT NULL,
            created_at REAL NOT NULL,
            curated INTEGER NOT NULL DEFAULT 0
        )""",
     "CREATE TABLE memory_removals (document_id TEXT PRIMARY KEY)"),
    # 7: the ledgers for images and reactions, and members' wishes about them (stage 7).
    ("""CREATE TABLE media_ledger (
            id INTEGER PRIMARY KEY,
            channel_id TEXT NOT NULL,
            sha TEXT NOT NULL,
            url TEXT NOT NULL,
            spontaneous INTEGER NOT NULL,
            posted_at REAL NOT NULL
        )""",
     "CREATE INDEX media_ledger_by_sha ON media_ledger (sha, posted_at)",
     "CREATE TABLE emoji_ledger (channel_id TEXT PRIMARY KEY, reacted_at REAL NOT NULL)",
     "CREATE TABLE member_prefs (author_id TEXT PRIMARY KEY, no_media INTEGER NOT NULL, updated_at REAL NOT NULL)"),
    # 8: when an overheard picture was shown to her, so that it is shown once (stage 8).
    ("ALTER TABLE files ADD COLUMN shown_at REAL",),
    # 9: which message a logged message replies to, so that her reply to a message kept out of
    # memory can be kept out with it.
    ("ALTER TABLE room_log ADD COLUMN reply_to TEXT",),
    # 10: for the monitor, which could not show them before: the names of channels (the server's
    # name goes in ``meta``), and the facts from memory she had before her for each reply.
    ("CREATE TABLE channel_names (channel_id TEXT PRIMARY KEY, name TEXT NOT NULL, last_seen REAL NOT NULL)",
     """CREATE TABLE recalled (
            id INTEGER PRIMARY KEY,
            message_id TEXT NOT NULL,
            source TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        )""",
     "CREATE INDEX recalled_by_message ON recalled (message_id)",
     "CREATE INDEX recalled_by_fact ON recalled (fact_id)",
     "CREATE INDEX recalled_by_age ON recalled (created_at)"),
)


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer version of the bot."""


def migrate(conn: sqlite3.Connection, migrations: tuple[tuple[str, ...], ...] = MIGRATIONS) -> int:
    """Apply any pending migrations. Returns the schema version afterwards.

    Each migration is one transaction: it lands completely, with its version
    number, or not at all.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(migrations):
        raise SchemaTooNewError(
            f"database is at schema {current}, this code only knows up to {len(migrations)}")
    for number in range(current + 1, len(migrations) + 1):
        conn.execute("BEGIN")
        try:
            for statement in migrations[number - 1]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return len(migrations)


def open_db(path: Path) -> sqlite3.Connection:
    """Open the database at ``path``, creating and migrating it as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: transactions are opened and closed explicitly.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


class Store:
    """Hands out one open database per server."""

    def __init__(self, data_dir: Path):
        self._dir = Path(data_dir) / "guilds"
        self._open: dict[str, sqlite3.Connection] = {}

    def guild(self, guild_id: str) -> sqlite3.Connection:
        if not (isinstance(guild_id, str) and guild_id.isdigit()):
            raise ValueError("a server id must be a string of digits")
        if guild_id not in self._open:
            self._open[guild_id] = open_db(self._dir / f"{guild_id}.db")
        return self._open[guild_id]

    def close(self) -> None:
        for conn in self._open.values():
            conn.close()
        self._open.clear()
