"""Ledgers: small records that enforce her limits.

- the media ledger: which image went where, for the repeat limit and the spontaneous cooldown
- the emoji ledger: when she last reacted in a channel
- member preferences she must honour in code, such as "no GIFs for me"

The media ledger holds links and fingerprints, never chat text.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def posted_since(conn: sqlite3.Connection, sha: str, since: float) -> bool:
    """Has this exact image been posted anywhere in the server since ``since``."""
    return conn.execute("SELECT 1 FROM media_ledger WHERE sha = ? AND posted_at >= ? LIMIT 1",
                        (sha, since)).fetchone() is not None


def last_spontaneous(conn: sqlite3.Connection, channel_id: str) -> float:
    """When she last posted an unasked-for image in this channel, or 0."""
    row = conn.execute("SELECT MAX(posted_at) FROM media_ledger WHERE channel_id = ? AND spontaneous = 1",
                       (channel_id,)).fetchone()
    return row[0] or 0.0


def record_image(conn: sqlite3.Connection, channel_id: str, sha: str, url: str, spontaneous: bool, at: float) -> None:
    conn.execute("INSERT INTO media_ledger (channel_id, sha, url, spontaneous, posted_at) VALUES (?, ?, ?, ?, ?)",
                 (channel_id, sha, url, int(spontaneous), at))


def expire_images(conn: sqlite3.Connection, before: float) -> int:
    return conn.execute("DELETE FROM media_ledger WHERE posted_at < ?", (before,)).rowcount


def last_reaction(conn: sqlite3.Connection, channel_id: str) -> float:
    row = conn.execute("SELECT reacted_at FROM emoji_ledger WHERE channel_id = ?", (channel_id,)).fetchone()
    return row[0] if row else 0.0


def record_reaction(conn: sqlite3.Connection, channel_id: str, at: float) -> None:
    conn.execute("INSERT INTO emoji_ledger (channel_id, reacted_at) VALUES (?, ?)"
                 " ON CONFLICT (channel_id) DO UPDATE SET reacted_at = excluded.reacted_at", (channel_id, at))


def wants_no_media(conn: sqlite3.Connection, author_id: str) -> bool:
    row = conn.execute("SELECT no_media FROM member_prefs WHERE author_id = ?", (author_id,)).fetchone()
    return bool(row and row[0])


def set_no_media(conn: sqlite3.Connection, author_id: str, no_media: Optional[bool], at: float) -> None:
    """Record a member's wish about unasked-for images. None means they said nothing about it."""
    if no_media is None:
        return
    conn.execute("INSERT INTO member_prefs (author_id, no_media, updated_at) VALUES (?, ?, ?)"
                 " ON CONFLICT (author_id) DO UPDATE SET no_media = excluded.no_media, updated_at = excluded.updated_at",
                 (author_id, int(no_media), at))
