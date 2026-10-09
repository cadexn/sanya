"""Starting and stopping her, by the same two scripts as the double-click launchers.

This is the one place where the monitor does anything to the bot, and it does it only when the
owner presses a button on the page. Nothing else in the monitor calls it.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import health
from .settings import Settings

SECONDS = 90  # the start script itself waits a few seconds to see that she stays up
Run = Callable[[Path], Awaitable[tuple[int, str]]]


def _powershell() -> str:
    """Windows PowerShell by its full path, so that nothing else of that name on the PATH is run."""
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    path = Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(path) if path.is_file() else "powershell.exe"


async def run_script(script: Path) -> tuple[int, str]:
    """Run one of the two scripts and give back its exit code and what it printed."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = await asyncio.create_subprocess_exec(
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        cwd=str(script.parent.parent), creationflags=flags)
    try:
        printed, _ = await asyncio.wait_for(process.communicate(), SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        return 1, "The script did not finish in time."
    return process.returncode or 0, printed.decode("utf-8", errors="replace").strip()


class Control:
    def __init__(self, settings: Settings, run: Optional[Run] = None,
                 state: Callable[[Settings], dict] = health.process):
        self._settings = settings
        self._run = run or run_script
        self._state = state
        self._lock = asyncio.Lock()
        self.last: Optional[dict] = None  # the last action taken from the page, for the page to show

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def act(self, action: str) -> dict:
        """Start or stop her. One action at a time; a second press while one is running is refused."""
        if action not in ("start", "stop"):
            return {"ok": False, "action": action, "output": "Unknown action."}
        if self._lock.locked():
            return {"ok": False, "action": action, "output": "Another start or stop is still in progress."}
        async with self._lock:
            now = self._state(self._settings)
            if action == "start" and now["state"] != "stopped":
                # A second copy would be refused by her own lock anyway; this says so plainly.
                done = {"ok": False, "action": action,
                        "output": f"Sanya is already running (process {now['pid']})."}
            else:
                script = self._settings.start_script if action == "start" else self._settings.stop_script
                if not script.is_file():
                    done = {"ok": False, "action": action, "output": f"The script is missing: {script}"}
                else:
                    try:
                        code, printed = await self._run(script)
                        done = {"ok": code == 0, "action": action, "output": printed}
                    except OSError as exc:
                        done = {"ok": False, "action": action, "output": f"Could not run the script: {exc}"}
            done["at"] = time.time()
            self.last = done
            return done
