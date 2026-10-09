"""Loads and validates config/sanya.yaml, and reads the bot token.

Every value the owner approved lives in the config file. There are no defaults
in code: a missing or unknown key is an error, so the file is the one place a
setting can be.
"""
from __future__ import annotations

import os
import types
import typing
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

TOKEN_VAR = "DISCORD_BOT_TOKEN"


class ConfigError(ValueError):
    """The config is missing a key, has an unknown one, or holds a bad value."""


@dataclass(frozen=True)
class Discord:
    owner_user_id: str
    active_servers: Optional[list[str]]
    bots_as_people: list[str]
    status_hours: int
    no_reply_channels: list[str]


@dataclass(frozen=True)
class Attachments:
    max_count: int
    max_mb: int
    open_hours: int
    download_seconds: int


@dataclass(frozen=True)
class Intake:
    batch_wait_seconds: float
    max_turn_restarts: Optional[int]
    bot_exchange_ceiling: Optional[int]
    attachments: Attachments


@dataclass(frozen=True)
class RoomLog:
    keep_days: int
    embed_chars: int


@dataclass(frozen=True)
class Context:
    minutes: int
    max_messages: int
    pictures: int
    line_chars: int
    quote_chars: int
    named_others: int


@dataclass(frozen=True)
class Conversation:
    window_messages: int
    keep_days: int


@dataclass(frozen=True)
class Model:
    base_url: str
    name: str
    max_calls_per_reply: int
    retries: int
    timeout_seconds: int
    max_reply_tokens: int
    thinking: bool
    tools_per_call: int


@dataclass(frozen=True)
class Tools:
    search_order: list[str]
    search_monthly_caps: dict[str, int]
    search_results: int
    search_chars: int
    page_chars: int
    page_max_bytes: int
    fetch_timeout_seconds: int
    max_redirects: int
    browser_identity: str
    look_back_lines: int
    names_in_searches: bool


@dataclass(frozen=True)
class Files:
    gif_frames: int
    max_megapixels: int
    max_gif_frames: int
    reminder_gb: int
    text_endings: list[str]
    describe_days: int
    description_chars: int


@dataclass(frozen=True)
class SecurityCheck:
    enabled: bool
    timeout_seconds: int
    flag_when_failed: bool


@dataclass(frozen=True)
class Media:
    spontaneous_chance: float
    cooldown_minutes: int
    repeat_hours: int
    optional_searches: int
    optional_candidates: int
    requested_candidates: int
    requested_images: int
    search_results: int
    download_seconds: int
    preview_px: int
    ledger_keep_days: int


@dataclass(frozen=True)
class Emoji:
    chance: float
    cooldown_minutes: int
    short_chars: int


@dataclass(frozen=True)
class Send:
    max_messages: int
    model_names: list[str]


@dataclass(frozen=True)
class Memory:
    hindsight_url: str
    banks: dict[str, str]
    recall_facts: int
    recall_lines: int
    recall_chars: int
    recall_asker_facts: int
    recall_own_facts: int
    quiet_minutes: int
    batch_messages: int
    flush_minutes: int
    hearsay_keep_days: int
    merge_idle_minutes: int
    merge_every_minutes: int
    list_facts: int
    back_off_minutes: int
    recall_seconds: int
    read_seconds: int
    write_seconds: int
    standing_wish_words: list[str]


@dataclass(frozen=True)
class Logs:
    keep_days: int


@dataclass(frozen=True)
class Paths:
    data_dir: str
    log_dir: str


@dataclass(frozen=True)
class Config:
    discord: Discord
    intake: Intake
    room_log: RoomLog
    context: Context
    conversation: Conversation
    model: Model
    tools: Tools
    files: Files
    security_check: SecurityCheck
    media: Media
    emoji: Emoji
    send: Send
    memory: Memory
    logs: Logs
    paths: Paths


def _positive(value: Any) -> bool:
    return value >= 1


def _not_negative(value: Any) -> bool:
    return value >= 0


def _chance(value: Any) -> bool:
    return 0 <= value <= 1


def _none_or_not_negative(value: Any) -> bool:
    return value is None or value >= 0


def _id(value: Any) -> bool:
    return value.isdigit()


def _id_or_empty(value: Any) -> bool:
    return value == "" or value.isdigit()


def _ids(value: Any) -> bool:
    return all(item.isdigit() for item in value)


def _none_or_ids(value: Any) -> bool:
    return value is None or all(item.isdigit() for item in value)


def _banks(value: Any) -> bool:
    return all(key.isdigit() and name.strip() for key, name in value.items())


SEARCH_SERVICES = ("brave", "tavily", "duckduckgo")


def _services(value: Any) -> bool:
    return bool(value) and all(item in SEARCH_SERVICES for item in value) and len(set(value)) == len(value)


def _caps(value: Any) -> bool:
    return all(key in SEARCH_SERVICES and count >= 0 for key, count in value.items())


def _url(value: Any) -> bool:
    return value.startswith(("http://", "https://"))


def _text(value: Any) -> bool:
    return bool(value.strip())


def _endings(value: Any) -> bool:
    return bool(value) and all(item.startswith(".") and len(item) > 1 for item in value)


def _words(value: Any) -> bool:
    return bool(value) and all(item.strip() for item in value)


def _words_or_none(value: Any) -> bool:
    return all(item.strip() for item in value)


# Dotted path -> (check, what the value must be).
_RULES: dict[str, tuple[Any, str]] = {
    "discord.owner_user_id": (_id_or_empty, "a Discord id in quotes, or empty"),
    "discord.active_servers": (_none_or_ids, "null or a list of Discord ids in quotes"),
    "discord.bots_as_people": (_ids, "a list of Discord ids in quotes"),
    "discord.status_hours": (_positive, "1 or more"),
    "discord.no_reply_channels": (_ids, "a list of Discord ids in quotes"),
    "intake.batch_wait_seconds": (_not_negative, "0 or more"),
    "intake.max_turn_restarts": (_none_or_not_negative, "null or 0 or more"),
    "intake.bot_exchange_ceiling": (_none_or_not_negative, "null or 0 or more"),
    "intake.attachments.max_count": (_not_negative, "0 or more"),
    "intake.attachments.max_mb": (_positive, "1 or more"),
    "intake.attachments.open_hours": (_positive, "1 or more"),
    "intake.attachments.download_seconds": (_positive, "1 or more"),
    "room_log.keep_days": (_positive, "1 or more"),
    "context.minutes": (_positive, "1 or more"),
    "context.max_messages": (_positive, "1 or more"),
    "context.pictures": (_not_negative, "0 or more"),
    "context.line_chars": (_positive, "1 or more"),
    "context.quote_chars": (_positive, "1 or more"),
    "context.named_others": (_not_negative, "0 or more"),
    "room_log.embed_chars": (_positive, "1 or more"),
    "conversation.window_messages": (_positive, "1 or more"),
    "conversation.keep_days": (_positive, "1 or more"),
    "model.base_url": (_url, "an http or https address"),
    "model.name": (_text, "a model name"),
    "model.max_calls_per_reply": (_positive, "1 or more"),
    "model.retries": (_not_negative, "0 or more"),
    "model.timeout_seconds": (_positive, "1 or more"),
    "model.max_reply_tokens": (_positive, "1 or more"),
    "model.tools_per_call": (_positive, "1 or more"),
    "security_check.timeout_seconds": (_positive, "1 or more"),
    "tools.search_order": (_services, "a list drawn from brave, tavily and duckduckgo, each once"),
    "tools.search_monthly_caps": (_caps, "search services mapped to a number of searches, 0 or more"),
    "tools.search_results": (_positive, "1 or more"),
    "tools.search_chars": (_positive, "1 or more"),
    "tools.page_chars": (_positive, "1 or more"),
    "tools.page_max_bytes": (_positive, "1 or more"),
    "tools.fetch_timeout_seconds": (_positive, "1 or more"),
    "tools.max_redirects": (_positive, "1 or more"),
    "tools.browser_identity": (_text, "a browser's User-Agent text"),
    "tools.look_back_lines": (_positive, "1 or more"),
    "files.gif_frames": (_positive, "1 or more"),
    "files.max_megapixels": (_positive, "1 or more"),
    "files.max_gif_frames": (_positive, "1 or more"),
    "files.reminder_gb": (_positive, "1 or more"),
    "files.text_endings": (_endings, "a list of file endings, each starting with a dot"),
    "files.describe_days": (_positive, "1 or more"),
    "files.description_chars": (_positive, "1 or more"),
    "media.spontaneous_chance": (_chance, "between 0 and 1"),
    "media.cooldown_minutes": (_not_negative, "0 or more"),
    "media.repeat_hours": (_not_negative, "0 or more"),
    "media.optional_searches": (_not_negative, "0 or more"),
    "media.optional_candidates": (_not_negative, "0 or more"),
    "media.requested_candidates": (_positive, "1 or more"),
    "media.requested_images": (_positive, "1 or more"),
    "media.search_results": (_positive, "1 or more"),
    "media.download_seconds": (_positive, "1 or more"),
    "media.preview_px": (_positive, "1 or more"),
    "media.ledger_keep_days": (_positive, "1 or more"),
    "emoji.short_chars": (_positive, "1 or more"),
    "emoji.chance": (_chance, "between 0 and 1"),
    "emoji.cooldown_minutes": (_not_negative, "0 or more"),
    "send.max_messages": (_positive, "1 or more"),
    "send.model_names": (_words_or_none, "a list of names, which may be empty"),
    "memory.hindsight_url": (_url, "an http or https address"),
    "memory.banks": (_banks, "server ids in quotes, each mapped to a bank name"),
    "memory.recall_facts": (_not_negative, "0 or more"),
    "memory.recall_lines": (_not_negative, "0 or more"),
    "memory.recall_chars": (_positive, "1 or more"),
    "memory.recall_asker_facts": (_not_negative, "0 or more"),
    "memory.recall_own_facts": (_not_negative, "0 or more"),
    "memory.quiet_minutes": (_not_negative, "0 or more"),
    "memory.batch_messages": (_positive, "1 or more"),
    "memory.flush_minutes": (_positive, "1 or more"),
    "memory.merge_idle_minutes": (_positive, "1 or more"),
    "memory.merge_every_minutes": (_positive, "1 or more"),
    "memory.hearsay_keep_days": (_positive, "1 or more"),
    "memory.list_facts": (_positive, "1 or more"),
    "memory.back_off_minutes": (_positive, "1 or more"),
    "memory.recall_seconds": (_positive, "1 or more"),
    "memory.read_seconds": (_positive, "1 or more"),
    "memory.write_seconds": (_positive, "1 or more"),
    "memory.standing_wish_words": (_words, "a list of words or phrases"),
    "logs.keep_days": (_positive, "1 or more"),
    "paths.data_dir": (_text, "a folder"),
    "paths.log_dir": (_text, "a folder"),
}


def _convert(value: Any, hint: Any, path: str) -> Any:
    """Check ``value`` against the type ``hint`` and return it in that type."""
    origin = typing.get_origin(hint)
    if is_dataclass(hint):
        return _build(hint, value, path)
    if origin is typing.Union or origin is types.UnionType:
        if value is None:
            return None
        (inner,) = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        return _convert(value, inner, path)
    if origin is list:
        (item,) = typing.get_args(hint)
        if not isinstance(value, list):
            raise ConfigError(f"{path} must be a list")
        return [_convert(entry, item, f"{path}[{i}]") for i, entry in enumerate(value)]
    if origin is dict:
        key_hint, value_hint = typing.get_args(hint)
        if not isinstance(value, dict):
            raise ConfigError(f"{path} must be a mapping")
        return {_convert(key, key_hint, f"{path} key {key!r}"): _convert(entry, value_hint, f"{path}.{key}")
                for key, entry in value.items()}
    if hint is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{path} must be true or false")
        return value
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{path} must be a whole number")
        return value
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} must be a number")
        return float(value)
    if hint is str:
        if not isinstance(value, str):
            raise ConfigError(f"{path} must be text (put ids in quotes)")
        return value
    raise ConfigError(f"{path} has a type this loader does not handle")


def _build(cls: type, data: Any, path: str) -> Any:
    """Build dataclass ``cls`` from a mapping whose keys must match its fields exactly."""
    where = path or "the config file"
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping of settings")
    hints = typing.get_type_hints(cls)
    names = [field.name for field in fields(cls)]
    prefix = f"{path}." if path else ""
    missing = [name for name in names if name not in data]
    if missing:
        raise ConfigError("missing settings: " + ", ".join(prefix + name for name in missing))
    unknown = [str(key) for key in data if key not in names]
    if unknown:
        raise ConfigError("unknown settings: " + ", ".join(prefix + key for key in unknown))
    return cls(**{name: _convert(data[name], hints[name], prefix + name) for name in names})


def _lookup(config: Config, dotted: str) -> Any:
    value: Any = config
    for part in dotted.split("."):
        value = getattr(value, part)
    return value


def parse_config(data: Any) -> Config:
    """Validate an already-parsed config mapping."""
    config = _build(Config, data, "")
    for dotted, (check, must_be) in _RULES.items():
        if not check(_lookup(config, dotted)):
            raise ConfigError(f"{dotted} must be {must_be}")
    return config


def load_config(path: Path) -> Config:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read the config file {path}: {exc.strerror}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"the config file {path} is not valid YAML") from exc
    return parse_config(data)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_token(env_file: Path) -> str:
    """Return the bot token from the environment, or from ``env_file``.

    The value never appears in an error or a log line.
    """
    token = os.environ.get(TOKEN_VAR, "").strip() or _read_env_file(env_file).get(TOKEN_VAR, "")
    if not token:
        raise ConfigError(f"{TOKEN_VAR} is not set. Put it in {env_file} (see .env.example).")
    return token
