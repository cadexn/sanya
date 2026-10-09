"""Event log: what happened, never what was said.

One JSON object per line in logs/events.log. Fields are ids, counts, durations
and short reason codes. Message text has no place here, so the field names it
usually travels under are refused outright: a careless call fails in tests
instead of leaking chat into a log.

One exception, by the owner's decision of 9 October 2026: the words of a search
she made herself, under ``words`` (see ``search_words``). A search is refused
before it is logged if it names a member.
"""
from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

FORBIDDEN_FIELDS = frozenset({"text", "content", "body", "message", "prompt", "reply", "query"})
MAX_STRING = 120
SEARCH_WORDS = 80  # how much of a search's words is kept

_logger = logging.getLogger("sanya.events")
_logger.propagate = False
_library_handlers: list[logging.Handler] = []
# The reply turn being worked on, if any. Events logged during it carry its id.
turn_id: contextvars.ContextVar[str] = contextvars.ContextVar("turn_id", default="")


def setup(log_dir: Path, keep_days: int, *, console: bool = True) -> None:
    """Send events to ``log_dir/events.log``, one file per day, ``keep_days`` kept."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    reset()
    handler = TimedRotatingFileHandler(log_dir / "events.log", when="midnight",
                                       backupCount=keep_days, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(stream)
    _logger.setLevel(logging.INFO)

    # Library warnings and errors go to their own file. Discord's library logs
    # message payloads at DEBUG, so it is never allowed below WARNING.
    library = TimedRotatingFileHandler(log_dir / "library.log", when="midnight",
                                       backupCount=keep_days, encoding="utf-8")
    library.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    library.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(library)
    root.setLevel(logging.WARNING)
    _library_handlers.append(library)


def reset() -> None:
    """Detach and close every handler this module added."""
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        handler.close()
    root = logging.getLogger()
    while _library_handlers:
        handler = _library_handlers.pop()
        root.removeHandler(handler)
        handler.close()


def search_words(query: str) -> str:
    """The words of a search as the log keeps them: one line, cut short."""
    return " ".join(query.split())[:SEARCH_WORDS]


def log_event(event: str, **fields: Any) -> None:
    """Record one event. Raises on a field that could carry message text."""
    for key, value in fields.items():
        if key in FORBIDDEN_FIELDS:
            raise ValueError(f"event field {key!r} could carry message text; log an id or a count")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise TypeError(f"event field {key!r} must be a plain value, not {type(value).__name__}")
        if isinstance(value, str) and (len(value) > MAX_STRING or "\n" in value or "\r" in value):
            raise ValueError(f"event field {key!r} is too long or multi-line for an event log")
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event}
    if turn_id.get():
        record["turn"] = turn_id.get()
    record.update(fields)
    _logger.info(json.dumps(record, ensure_ascii=True))
