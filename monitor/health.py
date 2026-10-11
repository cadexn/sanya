"""Is she running, are the services she leans on answering, and how much is stored.

Nothing here changes anything. The process is looked at, not touched; the two services are asked
for their health page and nothing else.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

from . import files
from .settings import Settings

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
    _kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION: enough to look, not enough to touch
    _STILL_ACTIVE = 259
    _ACCESS_DENIED = 5

    def inspect(pid: int) -> Optional[dict]:
        """What can be seen of a process: its program and when it began. None if there is no such process."""
        handle = _kernel.OpenProcess(_QUERY, False, pid)
        if not handle:
            # Refused means it exists but belongs to another account.
            return {"path": "", "since": None, "hidden": True} if ctypes.get_last_error() == _ACCESS_DENIED else None
        try:
            code = wintypes.DWORD()
            if not _kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None
            buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            path = buffer.value if _kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else ""
            created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
            since = None
            if _kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel),
                                       ctypes.byref(user)):
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                since = (ticks - 116444736000000000) / 10_000_000  # 100 ns ticks since 1601 -> seconds since 1970
            return {"path": path, "since": since, "hidden": False}
        finally:
            _kernel.CloseHandle(handle)
else:
    def inspect(pid: int) -> Optional[dict]:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return {"path": "", "since": None, "hidden": True}
        return {"path": "", "since": None, "hidden": False}


def _own_python(root: Path, path: str) -> bool:
    """Is this executable this project's own Python: under the project folder, or the interpreter
    its .venv was made from (the ``home`` line of .venv/pyvenv.cfg), which a launcher-style venv runs."""
    homes = [str(root)]
    try:
        for line in (root / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                homes.append(value.strip())
    except OSError:
        pass
    lowered = path.lower()
    return any(lowered.startswith(home.lower().rstrip(os.sep) + os.sep) for home in homes)


def process(settings: Settings, look: Callable[[int], Optional[dict]] = inspect) -> dict:
    """Whether the bot is running, judged as the Stop script judges it.

    The process recorded in ``sanya.pid`` counts only if it is this project's own Python: process
    numbers are reused, and a stale file may point at something else by now.
    """
    recorded = files.read_text(settings.data_dir / "sanya.pid").strip().splitlines()
    if not recorded or not recorded[0].strip().isdigit():
        return {"state": "stopped", "pid": None, "since": None}
    pid = int(recorded[0].strip())
    seen = look(pid)
    if seen is None:
        return {"state": "stopped", "pid": None, "since": None}
    if seen["hidden"]:
        # It exists but this account may not look at it: started under another Windows account.
        return {"state": "other_account", "pid": pid, "since": None}
    inside = _own_python(settings.root, seen["path"])
    if seen["path"] and not inside:
        return {"state": "stopped", "pid": None, "since": None}
    return {"state": "running", "pid": pid, "since": seen["since"]}


class Health:
    """The slower look-ups, each kept for a short while so that an open page does not repeat them every second."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._clock = clock
        self._session: Optional[aiohttp.ClientSession] = None
        self._kept: dict[str, tuple[float, Any]] = {}
        self._usage: dict = {}
        self._usage_read = False

    def _fresh(self, name: str, seconds: float) -> Any:
        kept = self._kept.get(name)
        return kept[1] if kept is not None and self._clock() - kept[0] < seconds else None

    def _keep(self, name: str, value: Any) -> Any:
        self._kept[name] = (self._clock(), value)
        return value

    async def _ask(self, url: str, seconds: float = 3.0) -> dict:
        """Fetch a health page. Returns whether it answered, how fast, and what it said."""
        if not url:
            return {"ok": False, "detail": "not set"}
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False)
        started = self._clock()
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=seconds)) as response:
                try:
                    said = await response.json(content_type=None)
                except ValueError:
                    said = None
                took = round((self._clock() - started) * 1000)
                return {"ok": response.status == 200, "ms": took,
                        "detail": said if isinstance(said, dict) else f"http {response.status}"}
        except asyncio.TimeoutError:
            return {"ok": False, "detail": f"no answer in {seconds:g} s"}
        except (aiohttp.ClientError, OSError) as exc:
            return {"ok": False, "detail": type(exc).__name__}

    async def services(self) -> dict:
        """The model server and the memory service: do they answer."""
        kept = self._fresh("services", 10)
        if kept is not None:
            return kept
        model, memory = await asyncio.gather(
            self._ask(self._settings.model_url + "/health" if self._settings.model_url else ""),
            self._ask(self._settings.hindsight_url + "/health" if self._settings.hindsight_url else ""))
        return self._keep("services", {"model": model, "memory": memory})

    def usage(self, busy: bool) -> dict:
        """Searches made this month, against each service's monthly allowance.

        The counts file is replaced by the bot on every search, and Windows will not let a file be
        replaced while another program has it open. A search only happens well into a reply turn,
        so the file is read only when the log, read a moment ago, shows no reply in progress.
        """
        if not busy:
            try:
                loaded = json.loads(files.read_text(self._settings.data_dir / "usage.json") or "{}")
            except ValueError:
                loaded = None
            if isinstance(loaded, dict):
                self._usage, self._usage_read = loaded, True
        month = datetime.now().strftime("%Y-%m")
        counts = self._usage.get(month) if isinstance(self._usage.get(month), dict) else {}
        services = sorted(set(counts) | set(self._settings.search_caps))
        return {"month": month, "read": self._usage_read,
                "services": [{"name": name, "used": counts.get(name, 0) if isinstance(counts.get(name, 0), int) else 0,
                              "cap": self._settings.search_caps.get(name)} for name in services]}

    async def storage(self) -> dict:
        """How much is on disk: the kept files, the databases and the logs."""
        kept = self._fresh("storage", 120)
        if kept is not None:
            return kept
        measured = await asyncio.get_running_loop().run_in_executor(None, _measure, self._settings)
        return self._keep("storage", measured)

    def console(self) -> str:
        """The last lines she wrote to her error output: why a start failed, when one did."""
        path = self._settings.log_dir / "console.err.log"
        tail = files.read_bytes(path, max(0, files.size(path) - 4000))
        return tail.decode("utf-8", errors="replace").strip()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


def _tree(folder: Path) -> tuple[int, int]:
    """The bytes and the number of files under a folder. Looking at a file's size does not open it."""
    total = count = 0
    stack = [folder]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def _measure(settings: Settings) -> dict:
    kept_bytes, kept_files = _tree(settings.data_dir / "files")
    databases = []
    for path in sorted((settings.data_dir / "guilds").glob("*.db")) if (settings.data_dir / "guilds").is_dir() else []:
        databases.append({"guild": path.stem,
                          "bytes": files.size(path) + files.size(path.with_name(path.name + "-wal"))})
    log_bytes, log_files = _tree(settings.log_dir)
    return {"files": {"bytes": kept_bytes, "count": kept_files}, "databases": databases,
            "logs": {"bytes": log_bytes, "count": log_files}}
