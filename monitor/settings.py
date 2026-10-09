"""What the monitor needs to know: its own few settings, and where the bot keeps things.

The bot's settings are read from ``config/sanya.yaml`` and never written. The file of secrets
beside the project is not read at all: nothing here needs a token or a key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path
    port: int = 7269
    history_days: int = 7
    open_browser: bool = True
    show_switched_off: bool = False  # list servers she is switched off in too
    servers: dict[str, str] = field(default_factory=dict)  # server id -> the name to show
    channels: dict[str, str] = field(default_factory=dict)  # channel id -> the name to show
    # Read from the bot's own settings.
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    active_servers: Optional[tuple[str, ...]] = None  # None: every server she is in
    banks: dict[str, str] = field(default_factory=dict)  # server id -> memory bank
    owner_id: str = ""
    hindsight_url: str = ""
    model_url: str = ""  # the model server, without the /v1 ending
    search_caps: dict[str, int] = field(default_factory=dict)
    batch_messages: int = 20
    flush_minutes: int = 10
    quiet_minutes: int = 2
    room_log_days: int = 7
    conversation_days: int = 30
    catch_up_minutes: int = 30  # how far back she reads a channel's chat when she starts

    @property
    def start_script(self) -> Path:
        return self.root / "scripts" / "Start-Sanya.ps1"

    @property
    def stop_script(self) -> Path:
        return self.root / "scripts" / "Stop-Sanya.ps1"


def _mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _section(source: dict[str, Any], name: str) -> dict[str, Any]:
    value = source.get(name)
    return value if isinstance(value, dict) else {}


def _number(value: Any, fallback: int, lowest: int = 1) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= lowest else fallback


def _labels(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(name) for key, name in value.items() if name}


def _under(root: Path, folder: Any, fallback: str) -> Path:
    path = Path(folder if isinstance(folder, str) and folder else fallback)
    return path if path.is_absolute() else root / path


def load(root: Path = ROOT) -> Settings:
    """Read the monitor's settings and the parts of the bot's that it shows. A missing file means the defaults."""
    root = Path(root)
    own = _mapping(root / "config" / "monitor.yaml")
    bot = _mapping(root / "config" / "sanya.yaml")
    discord, memory, model = _section(bot, "discord"), _section(bot, "memory"), _section(bot, "model")
    paths, tools = _section(bot, "paths"), _section(bot, "tools")
    active = discord.get("active_servers")
    base = str(model.get("base_url") or "").rstrip("/")
    caps = tools.get("search_monthly_caps")
    return Settings(
        root=root,
        port=_number(own.get("port"), 7269, lowest=1024),
        history_days=_number(own.get("history_days"), 7),
        open_browser=own.get("open_browser") is not False,
        show_switched_off=own.get("show_switched_off") is True,
        servers=_labels(own.get("servers")),
        channels=_labels(own.get("channels")),
        data_dir=_under(root, paths.get("data_dir"), "data"),
        log_dir=_under(root, paths.get("log_dir"), "logs"),
        active_servers=tuple(str(one) for one in active) if isinstance(active, list) else None,
        banks=_labels(memory.get("banks")),
        owner_id=str(discord.get("owner_user_id") or ""),
        hindsight_url=str(memory.get("hindsight_url") or "").rstrip("/"),
        model_url=base[:-3] if base.endswith("/v1") else base,
        search_caps={str(key): value for key, value in caps.items() if isinstance(value, int)}
        if isinstance(caps, dict) else {},
        batch_messages=_number(memory.get("batch_messages"), 20),
        flush_minutes=_number(memory.get("flush_minutes"), 10),
        quiet_minutes=_number(memory.get("quiet_minutes"), 2),
        room_log_days=_number(_section(bot, "room_log").get("keep_days"), 7),
        conversation_days=_number(_section(bot, "conversation").get("keep_days"), 30),
        catch_up_minutes=_number(_section(bot, "context").get("minutes"), 30),
    )
