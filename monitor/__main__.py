"""Start the monitor:  python -m monitor

    python -m monitor --no-browser     do not open the page
    python -m monitor --port 7300      listen on another port for this run

The page is served on this PC's own address only (127.0.0.1). Starting it a second time opens
the page of the one already running.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from aiohttp import web

from .server import Monitor
from .settings import ROOT, load

MARK = '<meta name="application-name" content="Sanya Monitor">'


def _already_there(url: str) -> str:
    """What is at the address: "monitor", "other" (some other program has the port) or "" (nothing)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never by way of a proxy
    try:
        with opener.open(url, timeout=2) as response:
            return "monitor" if MARK in response.read(4000).decode("utf-8", errors="replace") else "other"
    except urllib.error.HTTPError:
        return "other"
    except (OSError, ValueError):
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Sanya Monitor: a page showing what the bot is doing")
    parser.add_argument("--port", type=int, help="listen on this port instead of the one in config/monitor.yaml")
    parser.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # another project folder, for trying it out
    args = parser.parse_args(argv)

    settings = load(Path(args.root))
    port = args.port or settings.port
    url = f"http://127.0.0.1:{port}/"
    show = settings.open_browser and not args.no_browser

    there = _already_there(url)
    if there == "monitor":
        print(f"Sanya Monitor is already running at {url}")
        if show:
            webbrowser.open(url)
        return 0
    if there == "other":
        print(f"Port {port} is in use by another program. Change 'port' in config\\monitor.yaml.", file=sys.stderr)
        return 1

    monitor = Monitor(settings)
    monitor.log.load()
    app = monitor.app()

    async def opened(app: web.Application) -> None:
        print(f"Sanya Monitor is running at {url}")
        print("It reads what Sanya records and changes nothing.")
        print("Close this window to stop the monitor. Sanya keeps running.")
        if show:
            asyncio.get_running_loop().call_later(0.5, webbrowser.open, url)

    app.on_startup.append(opened)
    try:
        web.run_app(app, host="127.0.0.1", port=port, print=None, access_log=None)
    except OSError as exc:
        print(f"Could not listen on port {port}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
