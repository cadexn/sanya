"""Start the bot:  python -m sanya

    python -m sanya --check    validate the config and storage, then exit
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import events, lock
from .config import ConfigError, load_config, load_token
from .persona import load_persona
from .store.db import Store

ROOT = Path(__file__).resolve().parent.parent


def _under_root(folder: str) -> Path:
    path = Path(folder)
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sanya", description="Sanya, a standalone Discord bot")
    parser.add_argument("--config", default=str(ROOT / "config" / "sanya.yaml"))
    parser.add_argument("--quiet", action="store_true", help="write events to the log file only")
    parser.add_argument("--check", action="store_true",
                        help="validate the config and storage, then exit without connecting")
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
        persona = load_persona(ROOT / "persona")
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    store = Store(_under_root(config.paths.data_dir))
    if args.check:
        for guild_id in config.memory.banks:
            store.guild(guild_id)
        store.close()
        print("config ok; persona ok; storage ok")
        return 0

    try:
        token = load_token(ROOT / ".env")
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    data_dir = _under_root(config.paths.data_dir)
    held = lock.hold(data_dir / "sanya.lock")
    if held is None:
        print("Sanya is already running. Stop that copy first.", file=sys.stderr)
        return 3
    (data_dir / "sanya.pid").write_text(str(os.getpid()), encoding="ascii")

    events.setup(_under_root(config.paths.log_dir), config.logs.keep_days, console=not args.quiet)
    events.log_event("starting")

    # Imported late so --check works without Discord's library.
    from dataclasses import replace

    from .client import SanyaClient
    from .config import _read_env_file
    from .intake.attachments import FileStore
    from .intake.bots import BotExchanges
    from .intake.security import SecurityCheck
    from .memory.ambient import Ambient
    from .memory.hindsight import Hindsight
    from .model.client import ModelClient
    from .store.usage import Usage
    from .tools.lookback import LookBack
    from .tools.media import Media
    from .tools.memory import MemoryTools
    from .tools.toolbox import Toolbox
    from .tools.web import Web
    from .turn import Replier

    # The check answers with one word: no thinking, a short limit, and no retry,
    # because a check that fails is treated as flagged. Temperature 0 makes it give
    # the same verdict for the same message every time.
    check_model = ModelClient(replace(config.model, thinking=False, retries=0, max_reply_tokens=8,
                                      timeout_seconds=config.security_check.timeout_seconds), purpose="check",
                              temperature=0)
    security = SecurityCheck(config.security_check.enabled, check_model, persona.security_check,
                             config.security_check.flag_when_failed)
    exchanges = BotExchanges(config.discord.bots_as_people, config.intake.bot_exchange_ceiling)
    # Every value in .env is a secret she must never post.
    env = _read_env_file(ROOT / ".env")
    secrets = [value for value in env.values() if len(value) > 8]
    keys = {"brave": env.get("BRAVE_API_KEY", ""), "tavily": env.get("TAVILY_API_KEY", "")}
    files = FileStore(data_dir, store, config.intake.attachments, config.files, config.tools)
    hindsight = Hindsight(config.memory.hindsight_url, recall_seconds=config.memory.recall_seconds,
                          read_seconds=config.memory.read_seconds, write_seconds=config.memory.write_seconds,
                          api_key=os.environ.get("HINDSIGHT_API_KEY", "").strip() or env.get("HINDSIGHT_API_KEY", ""))
    usage = Usage(data_dir / "usage.json")
    media = Media(config, {"klipy": env.get("KLIPY_API_KEY", ""), "giphy": env.get("GIPHY_API_KEY", ""),
                           "brave": keys["brave"]}, usage, store)
    toolbox = Toolbox(Web(config.tools, keys, usage), files, config.tools.page_chars,
                      MemoryTools(hindsight, store, config.memory.recall_facts, config.memory.list_facts), media,
                      LookBack(config, store, files), names_in_searches=config.tools.names_in_searches)
    events.log_event("search_services", brave=bool(keys["brave"]), tavily=bool(keys["tavily"]),
                     klipy=bool(env.get("KLIPY_API_KEY")), giphy=bool(env.get("GIPHY_API_KEY")))

    # Describing a picture is one short answer: no thinking, no retry, the same words every time.
    describer = ModelClient(replace(config.model, thinking=False, retries=0, max_reply_tokens=200),
                            purpose="describe", temperature=0)

    async def describe(pictures: list[str]) -> str:
        shown = [{"type": "image_url", "image_url": {"url": url}} for url in pictures]
        answer = await describer.complete([
            {"role": "system", "content": persona.image_description},
            {"role": "user", "content": [{"type": "text", "text": "Describe this."}, *shown]}])
        return answer.text

    def channel_name(channel_id: str) -> str:
        return getattr(client.get_channel(int(channel_id)), "name", None) or "chat"

    ambient = Ambient(config, store, files, hindsight, describe,
                      busy=lambda: client.batcher.busy, channel_name=channel_name)
    replier = Replier(config, store, persona, ModelClient(config.model), security, exchanges, files, toolbox,
                      secrets, hindsight=hindsight, on_activity=ambient.touch, media=media)
    client = SanyaClient(config, store, replier, exchanges, files, ambient, persona.statuses)
    try:
        client.run(token, log_handler=None)
    finally:
        store.close()
        events.log_event("stopped")
        held.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
