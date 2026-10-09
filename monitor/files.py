"""Reading the bot's files without getting in her way.

On Windows a file that one program holds open cannot be renamed, replaced or rewritten by another
unless it was opened with leave to. She renames her event log at midnight, replaces her search
counts on every search and writes her process number when she starts; a monitor holding one of
those open at the wrong moment would make that fail. So every file of hers is opened here with
all three kinds of sharing allowed, read, and closed again at once.

Her databases are a separate matter: they are opened read-only by SQLite, whose readers do not
hold up a writer when the database is in write-ahead mode, as hers are.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _SHARE_ALL = 0x1 | 0x2 | 0x4  # others may read, write, and rename or delete
    _OPEN_EXISTING = 3
    _INVALID = ctypes.c_void_p(-1).value

    _create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    _create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create_file.restype = wintypes.HANDLE
    _close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]

    def open_shared(path: Path) -> BinaryIO:
        """Open ``path`` for reading, leaving every other program free to write, rename or replace it."""
        handle = _create_file(str(path), _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, 0x80, None)
        if handle is None or handle == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _close_handle(handle)
            raise
        return os.fdopen(descriptor, "rb")
else:
    def open_shared(path: Path) -> BinaryIO:
        return open(path, "rb")


def read_bytes(path: Path, start: int = 0, limit: int | None = None) -> bytes:
    """The bytes of ``path`` from ``start`` on; empty if the file is not there or cannot be read."""
    try:
        with open_shared(Path(path)) as handle:
            if start:
                handle.seek(start)
            return handle.read() if limit is None else handle.read(limit)
    except OSError:
        return b""


def read_text(path: Path) -> str:
    return read_bytes(path).decode("utf-8", errors="replace")


def size(path: Path) -> int:
    """The size of a file in bytes, or 0 if it is not there. Looking does not open the file."""
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def open_database(path: Path) -> sqlite3.Connection:
    """Open one of her databases for reading only. Raises ``sqlite3.Error`` if it cannot be read."""
    path = Path(path)
    if not path.is_file():
        raise sqlite3.OperationalError("no such database")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")  # a second lock on the door: no statement may write
    return conn
