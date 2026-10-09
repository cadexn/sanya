"""Try the monitor's page on made-up data:  .venv\\Scripts\\python.exe monitor\\demo\\demo.py

Nothing of the real bot is read or touched. The page shows the invented server from
``world.py``; a stand-in answers for the memory service and the model server; and the Start and
Stop buttons only pretend.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

from aiohttp import web

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(HERE)]

import world as made_up  # noqa: E402
from monitor.control import Control  # noqa: E402
from monitor.server import Monitor  # noqa: E402
from monitor.settings import load  # noqa: E402


async def serve(port: int, folder: Path, stopped: bool) -> None:
    now = time.time()
    services = f"http://127.0.0.1:{port + 1}"
    world = made_up.build(folder, now, hindsight_url=services, model_url=services + "/v1", extras=True)
    (folder / "scripts").mkdir(exist_ok=True)
    for name in ("Start-Sanya.ps1", "Stop-Sanya.ps1"):
        (folder / "scripts" / name).write_text("# stands in for the real script; never run\n", encoding="ascii")
    fake = made_up.FakeMemory(world)

    async def stand_in(request: web.Request) -> web.Response:
        if request.path == "/health":
            return web.json_response({"status": "ok"})
        status, data = fake.answer(request.path, dict(request.query))
        return web.json_response(data, status=status)

    services_app = web.Application()
    services_app.router.add_get("/{tail:.*}", stand_in)

    state = {"state": "stopped" if stopped else "running", "pid": None if stopped else 4242,
             "since": None if stopped else now - 3 * 3600}

    def process(settings: object) -> dict:
        return dict(state)

    async def pretend(script: Path) -> tuple[int, str]:
        await asyncio.sleep(1.5)
        if "Start" in script.name:
            state.update(state="running", pid=4243, since=time.time())
            return 0, "Sanya is running (process 4243).\n(Made-up data: nothing was started.)"
        state.update(state="stopped", pid=None, since=None)
        return 0, "Sanya stopped (process 4242).\n(Made-up data: nothing was stopped.)"

    settings = replace(load(folder), port=port)
    monitor = Monitor(settings, control=Control(settings, run=pretend, state=process), process=process, token="demo-key")
    monitor.log.load()
    for app, at in ((services_app, port + 1), (monitor.app(), port)):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", at).start()
    print(f"Made-up monitor at http://127.0.0.1:{port}/  (stand-in services on {port + 1})", flush=True)
    await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7399)
    parser.add_argument("--dir", default=str(ROOT / ".monitor-demo"))
    parser.add_argument("--stopped", action="store_true", help="show her as stopped")
    args = parser.parse_args()
    folder = Path(args.dir)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    try:
        asyncio.run(serve(args.port, folder, args.stopped))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
