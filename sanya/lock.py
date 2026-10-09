"""One running copy at a time.

Two copies signed in with the same token would both answer every message. The
lock is held on a file for as long as the process lives, and the operating
system releases it when the process ends, however it ends.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Optional


def hold(path: Path) -> Optional[IO]:
    """Take the lock at ``path``. Returns the open file to keep, or None if another copy holds it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
