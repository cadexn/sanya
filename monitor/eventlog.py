"""The event log, read as it grows and folded into the things the page shows.

The log holds one line per thing that happened: ids, counts and durations, never what was said.
Lines that belong together are folded into one item: every event of a reply turn into that turn,
a picture being kept and then described into that picture, the events of a start into that start.
An item's ``rev`` is the number of events read when it last changed, so the page can ask for
"whatever changed since I last looked".
"""
from __future__ import annotations

import json
import re
import statistics
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import files

HEAD = 48  # the first bytes of a log file hold its first timestamp, which tells one day's file from the next
ROLLED = re.compile(r"^events\.log\.(\d{4}-\d{2}-\d{2})$")
START_WINDOW = 120  # seconds after "starting" in which connecting and catching up belong to that start
STALE_TURN = 900  # a turn with no ending after this long is no longer counted as in progress
RECENT = 60  # heard messages remembered per channel, to find the ones merged into a turn
CHANGES_KEPT = 20000
# Events that mean something went wrong on the way.
TROUBLE = frozenset({"model_call_failed", "recall_failed", "search_failed", "search_skipped", "image_search_failed",
                     "image_failed", "sticker_failed", "reaction_failed", "handler_error", "memory_failed", "describe_failed",
                     "files_folder_large"})
# Of those, the ones that are serious when they happen inside a reply turn: the rest she got over.
SERIOUS_IN_A_TURN = frozenset({"recall_failed", "handler_error"})
NOTICES = frozenset({"files_folder_large"})  # worth a look, not a failure
# What a turn records of its own choices, and of what came of them.
CHOICES = ("image_mode", "image_roll", "image_cooldown", "no_media", "reaction_allowed", "reaction_roll",
           "reaction_short", "reaction_cooldown", "tools", "memory_tools", "quote", "attachments")
OUTCOMES = ("image_outcome", "reaction_outcome", "tool_calls", "tools_refused")
_NOT_A_FIELD =frozenset({"ts", "event", "turn"})
_PLACE = frozenset({"guild_id", "channel_id"})


def _when(stamp: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _rest(fields: dict) -> dict:
    """An event's own fields, without the server and channel, which every item carries by itself."""
    return {key: value for key, value in fields.items() if key not in _PLACE}


def _middle(values: list[float]) -> Optional[float]:
    return round(statistics.median(values)) if values else None


def _high(values: list[float]) -> Optional[float]:
    """The value nine in ten fall under."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))])


class EventLog:
    def __init__(self, log_dir: Path, days: int = 7, clock: Callable[[], float] = time.time):
        self._dir = Path(log_dir)
        self._days = days
        self._clock = clock
        self._offset = 0  # how far into events.log reading has got
        self._head = b""  # the first bytes of the events.log being read
        self.seq = 0
        self.oldest_change = 0  # changes before this count are no longer listed one by one
        self.items: dict[str, dict] = {}
        self._order: list[str] = []  # item ids, oldest first
        self._changed: list[tuple[int, str]] = []
        self._open: dict[str, str] = {}  # channel -> the turn in progress there
        self._recent: dict[str, list[str]] = {}  # channel -> the last messages heard there
        self._start: Optional[dict] = None  # the latest start
        self._describe: Optional[dict] = None  # a picture's model call, until its "described" event names the server
        self.calls: list[dict] = []  # every model call, for the health view
        self._batches: list[dict] = []  # every memory batch
        self.first_at = 0.0  # the earliest event read: nothing is known of what came before
        self.starts: list[float] = []  # every time she started
        self.her_id = ""
        self.started_at = 0.0
        self.connected_at = 0.0
        self.stopped_at = 0.0
        self.last_at = 0.0

    # --- reading ---------------------------------------------------------------

    def load(self) -> None:
        """Read the kept days of rolled-over logs, oldest first, then the current one."""
        first_day = (date.fromtimestamp(self._clock()) - timedelta(days=self._days)).isoformat()
        rolled = []
        try:
            names = [entry.name for entry in self._dir.iterdir()]
        except OSError:
            names = []
        for name in names:
            found = ROLLED.match(name)
            if found and found.group(1) >= first_day:
                rolled.append(name)
        for name in sorted(rolled):
            self._fold_lines(files.read_bytes(self._dir / name))
        self.refresh()

    def refresh(self) -> bool:
        """Read what has been added to the log. Returns True if anything was."""
        before = self.seq
        for _ in range(2):  # the second pass reads the new file after a roll-over
            if not self._read_current():
                break
        return self.seq != before

    def _read_current(self) -> bool:
        """Read on from where the last read stopped. Returns True if the log had rolled over meanwhile."""
        try:
            with files.open_shared(self._dir / "events.log") as handle:
                head = handle.read(HEAD)
                if self._head and not head.startswith(self._head):
                    rolled = True
                else:
                    rolled = False
                    if not self._head:
                        self._head = head
                    handle.seek(self._offset)
                    data = handle.read()
        except OSError:
            return False
        if rolled:
            self._finish_rolled()
            self._offset, self._head = 0, b""
            return True
        whole = data[:data.rfind(b"\n") + 1]  # a line still being written is left for the next read
        self._offset += len(whole)
        self._fold_lines(whole)
        return False

    def _finish_rolled(self) -> None:
        """The file being read was renamed at midnight: read the rest of it under its new name."""
        try:
            names = sorted((entry.name for entry in self._dir.iterdir() if ROLLED.match(entry.name)), reverse=True)
        except OSError:
            return
        for name in names:
            path = self._dir / name
            if files.read_bytes(path, 0, HEAD).startswith(self._head):
                self._fold_lines(files.read_bytes(path, self._offset))
                return

    def _fold_lines(self, data: bytes) -> None:
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                self.fold(row)

    # --- folding ---------------------------------------------------------------

    def fold(self, row: dict) -> None:
        """Take one event into the items."""
        kind, at = row.get("event"), _when(row.get("ts"))
        if not isinstance(kind, str) or at is None:
            return
        self.seq += 1
        self.first_at = min(self.first_at, at) if self.first_at else at
        self.last_at = max(self.last_at, at)
        fields = {key: value for key, value in row.items() if key not in _NOT_A_FIELD}
        turn = row.get("turn")
        if kind == "turn_started":
            self._turn_started(at, str(turn or f"{row.get('message_id')}-{row.get('restarts', 0)}"), fields)
        elif turn:
            self._turn_step(kind, at, str(turn), fields)
        else:
            self._HANDLERS.get(kind, EventLog._other)(self, kind, at, fields)
        if len(self._changed) > CHANGES_KEPT * 2:
            self._changed = self._changed[-CHANGES_KEPT:]
            self.oldest_change = self._changed[0][0]

    def _touch(self, item: dict) -> None:
        item["rev"] = self.seq
        self._changed.append((self.seq, item["id"]))

    def _add(self, item_id: str, kind: str, at: float, guild: Any = None, channel: Any = None, **more: Any) -> dict:
        item = self.items.get(item_id)
        if item is None:
            item = {"id": item_id, "kind": kind, "at": at, "guild": str(guild) if guild else None,
                    "channel": str(channel) if channel else None}
            self.items[item_id] = item
            self._order.append(item_id)
        item.update(more)
        self._touch(item)
        return item

    # messages

    def _admitted(self, kind: str, at: float, fields: dict) -> None:
        message = str(fields.get("message_id"))
        item = self._add("m:" + message, "heard", at, fields.get("guild_id"), fields.get("channel_id"),
                         message=message, author=str(fields.get("author_id") or ""),
                         bot=bool(fields.get("author_is_bot")))
        if item["channel"]:
            recent = self._recent.setdefault(item["channel"], [])
            recent.append(item["id"])
            del recent[:-RECENT]

    def _dropped(self, kind: str, at: float, fields: dict) -> None:
        self._add(f"d:{self.seq}", "dropped", at, fields.get("guild_id"), fields.get("channel_id"),
                  message=str(fields.get("message_id") or ""), author=str(fields.get("author_id") or ""),
                  bot=bool(fields.get("author_is_bot")), reason=fields.get("reason"))

    def _addressed(self, kind: str, at: float, fields: dict) -> None:
        item = self.items.get("m:" + str(fields.get("message_id")))
        if item is not None:
            item["addressed" if kind == "message_addressed" else "unanswered"] = fields.get("reason")
            self._touch(item)

    # reply turns

    def _turn(self, turn: str, at: float, fields: dict) -> dict:
        item = self.items.get("t:" + turn)
        if item is None:
            # Seen first by a later event: the log being read began in the middle of the turn.
            message = turn.rsplit("-", 1)[0]
            heard = self.items.get("m:" + message) or {}
            item = self._add("t:" + turn, "turn", at, fields.get("guild_id") or heard.get("guild"),
                             fields.get("channel_id") or heard.get("channel"), turn=turn, message=message,
                             messages=1, restarts=0, state="running", steps=[], calls=0, tools=[], problems=0,
                             tokens=0)
        return item

    def _turn_started(self, at: float, turn: str, fields: dict) -> None:
        item = self._turn(turn, at, fields)
        item.update(guild=str(fields.get("guild_id") or item["guild"] or "") or None,
                    channel=str(fields.get("channel_id") or item["channel"] or "") or None,
                    message=str(fields.get("message_id") or item["message"]),
                    messages=int(fields.get("messages") or 1), restarts=int(fields.get("restarts") or 0))
        channel = item["channel"]
        if channel:
            self._close(channel, "unfinished", but=item["id"])
            self._open[channel] = item["id"]
        self._link(item)
        self._touch(item)

    def _link(self, item: dict) -> None:
        """Tie the messages a turn answers to it: the one it is named after, and those merged into it."""
        heard = self.items.get("m:" + item["message"])
        if heard is None:
            return
        heard["turn"] = item["turn"]
        self._touch(heard)
        item["author"] = heard.get("author")
        need = item["messages"] - 1
        for other_id in reversed(self._recent.get(item["channel"] or "", [])):
            if need <= 0:
                break
            other = self.items.get(other_id)
            if other is None or other is heard or other.get("author") != heard.get("author"):
                continue
            if not other.get("addressed"):
                continue
            earlier = self.items.get("t:" + str(other.get("turn"))) if other.get("turn") else None
            if earlier is None or earlier["state"] in ("restarted", "unfinished"):
                other["turn"] = item["turn"]
                self._touch(other)
                need -= 1

    def _close(self, channel: str, state: str, but: str = "") -> None:
        """The turn in progress in a channel, if any, ended without a reply."""
        open_id = self._open.get(channel)
        if open_id and open_id != but:
            item = self.items.get(open_id)
            if item is not None and item["state"] == "running":
                item["state"] = state
                self._touch(item)
            del self._open[channel]

    def _turn_step(self, kind: str, at: float, turn: str, fields: dict) -> None:
        item = self._turn(turn, at, fields)
        step = {**_rest(fields), "what": kind, "at": at}
        item["steps"].append(step)
        if kind in TROUBLE or (kind == "tool_call" and fields.get("ok") is False):
            step["trouble"] = True
            item["problems"] += 1
        if kind == "security_check":
            item["flagged"], item["reason"] = bool(fields.get("flagged")), fields.get("reason")
        elif kind == "turn_choices":
            # What the turn was allowed and on what dice: she records it since her first start after 9 October 2026.
            item["choices"] = {key: fields.get(key) for key in CHOICES}
            item["allowed"] = [what for what, on in (("image", fields.get("image_mode") == "optional"),
                                                     ("reaction", fields.get("reaction_allowed"))) if on]
        elif kind == "recall":
            item["facts"] = fields.get("facts")
        elif kind in ("model_call", "model_call_failed"):
            self._call(at, item["guild"], fields, failed=kind == "model_call_failed")
            if kind == "model_call" and fields.get("purpose") == "reply":
                item["calls"] += 1
                item["tokens"] += int(fields.get("completion_tokens") or 0)
                if "offered" in fields:
                    item.setdefault("offered", []).append(fields["offered"])  # tools offered on each call, in order
        elif kind == "tool_call":
            item["tools"].append(str(fields.get("name")))
        elif kind in ("search", "search_failed", "image_search", "image_search_failed"):
            # The words of each search she made for this reply, once each: she records them since 9 October 2026.
            words = fields.get("words")
            if words and str(words) not in item.setdefault("searched", []):
                item["searched"].append(str(words))
        elif kind == "reply_replaced":
            item["replaced"] = fields.get("reason")
        elif kind == "image_posted":
            item["image"] = True
        elif kind == "sticker_sent":
            item["sticker"] = "alone" if fields.get("alone") else "sent"
        elif kind == "reaction_added":
            item["reaction"] = "alone" if fields.get("alone") else "added"
        elif kind == "memory_held_back":
            item["held_back"] = fields.get("messages")
        elif kind in ("reply_sent", "reply_dropped"):
            if kind == "reply_sent":
                item["state"] = "failed" if fields.get("model_failed") else "sent"
                item["ms"] = fields.get("ms")
                item["sent"] = {key: fields.get(key) for key in
                                ("sent", "chars", "cut", "plain", "window", "overheard", "pictures", "tools")}
                item["sent"].update({key: fields[key] for key in OUTCOMES if key in fields})
            else:
                item["state"], item["dropped"] = "dropped", fields.get("reason")
            item["ended"] = at
            if item["channel"] and self._open.get(item["channel"]) == item["id"]:
                del self._open[item["channel"]]
        self._touch(item)

    def _restarted(self, kind: str, at: float, fields: dict) -> None:
        self._close(str(fields.get("channel_id")), "restarted")

    def _call(self, at: float, guild: Optional[str], fields: dict, failed: bool = False) -> dict:
        call = {"at": at, "guild": guild, "purpose": fields.get("purpose"), "ms": fields.get("ms") or 0,
                "failed": failed, "error": fields.get("error"),
                "prompt_tokens": fields.get("prompt_tokens") or 0, "completion_tokens": fields.get("completion_tokens") or 0,
                "prompt_ms": fields.get("prompt_ms") or 0, "predicted_ms": fields.get("predicted_ms") or 0}
        self.calls.append(call)
        return call

    def _loose_call(self, kind: str, at: float, fields: dict) -> None:
        # A model call outside a turn is a picture being described; its server is named by the event that follows.
        self._describe = self._call(at, None, fields, failed=kind == "model_call_failed")

    # background work

    def _background(self, kind: str, at: float, fields: dict) -> None:
        item = self._add(f"b:{self.seq}", "memory", at, fields.get("guild_id"), fields.get("channel_id"), what=kind,
                         trouble=kind in TROUBLE, fields=_rest(fields))
        if kind == "memory_batch":
            self._batches.append(item)

    def _picture(self, kind: str, at: float, fields: dict) -> None:
        guild, file_id = fields.get("guild_id"), fields.get("file_id")
        item_id = f"p:{guild}:{file_id}"
        if kind == "file_stored":
            self._add(item_id, "picture", at, guild, fields.get("channel_id"), file=file_id, mime=fields.get("mime"),
                      bytes=fields.get("bytes"), stored=fields.get("kind"))
            return
        item = self.items.get(item_id) or self._add(item_id, "picture", at, guild, None, file=file_id)
        if kind == "file_described":
            item.update(described=at, chars=fields.get("chars"), ms=fields.get("ms"))
        else:
            item["trouble"] = True
        if self._describe is not None:
            self._describe["guild"] = item["guild"]
            self._describe = None
        self._touch(item)

    # starting and stopping

    def _starting(self, kind: str, at: float, fields: dict) -> None:
        for channel in list(self._open):
            self._close(channel, "interrupted")  # she was stopped in the middle of answering
        self._start = self._add(f"s:{self.seq}", "system", at, what="start", servers={}, caught_up={}, services=[])
        self.started_at, self.stopped_at = at, 0.0
        self.starts.append(at)

    def _starting_still(self, at: float) -> Optional[dict]:
        return self._start if self._start is not None and at - self._start["at"] <= START_WINDOW else None

    def _lifecycle(self, kind: str, at: float, fields: dict) -> None:
        start = self._starting_still(at)
        guild = str(fields["guild_id"]) if fields.get("guild_id") else None
        if kind == "connected":
            self.her_id = str(fields.get("user_id") or self.her_id)
            self.connected_at = at
            if start is not None and "connected" not in start:
                start.update(connected=at, guilds=fields.get("guilds"))
            else:
                self._add(f"s:{self.seq}", "system", at, what="reconnected", fields={"guilds": fields.get("guilds")})
                return
        elif kind == "stopped":
            self.stopped_at = at
            for channel in list(self._open):
                self._close(channel, "interrupted")
            self._add(f"s:{self.seq}", "system", at, what="stopped", fields={})
            return
        elif start is not None and kind == "search_services":
            start["services"] = sorted(name for name, on in fields.items() if on is True)
        elif start is not None and kind in ("guild_ready", "guild_inactive") and guild:
            start["servers"][guild] = "ready" if kind == "guild_ready" else "inactive"
        elif start is not None and kind == "caught_up" and guild:
            start["caught_up"][guild] = fields.get("messages")
        elif start is not None and kind == "status_changed" and not start.get("status"):
            start["status"] = True
        else:
            self._add(f"s:{self.seq}", "system", at, guild, fields.get("channel_id"), what=kind, fields=_rest(fields))
            return
        self._touch(start)

    def _problem(self, kind: str, at: float, fields: dict) -> None:
        channel = str(fields["channel_id"]) if fields.get("channel_id") else None
        guild = fields.get("guild_id")
        if kind == "handler_error" and fields.get("where") == "turn" and channel and channel in self._open:
            turn = self.items[self._open[channel]]
            turn["steps"].append({"what": kind, "at": at, "trouble": True, "error": fields.get("error")})
            turn["problems"] += 1
            guild = guild or turn["guild"]
            self._close(channel, "error")
        self._add(f"x:{self.seq}", "problem", at, guild, channel, what=kind, trouble=True, fields=_rest(fields))

    def _other(self, kind: str, at: float, fields: dict) -> None:
        """An event this monitor has no special place for: shown as it is, so nothing she logs goes unseen."""
        self._add(f"o:{self.seq}", "problem" if kind in TROUBLE else "system", at, fields.get("guild_id"),
                  fields.get("channel_id"), what=kind, trouble=kind in TROUBLE, fields=_rest(fields))

    _HANDLERS: dict[str, Callable[["EventLog", str, float, dict], None]] = {
        "message_admitted": _admitted, "message_dropped": _dropped,
        "message_addressed": _addressed, "message_not_answered": _addressed,
        "turn_restarted": _restarted,
        "model_call": _loose_call, "model_call_failed": _loose_call,
        "memory_batch": _background, "memory_taken_back": _background, "memory_failed": _background,
        "memory_merge_asked": _background, "hearsay_expired": _background, "message_deleted": _background,
        "file_stored": _picture, "file_described": _picture, "describe_failed": _picture,
        "starting": _starting,
        "connected": _lifecycle, "stopped": _lifecycle, "search_services": _lifecycle, "guild_ready": _lifecycle,
        "guild_inactive": _lifecycle, "caught_up": _lifecycle, "status_changed": _lifecycle,
        "room_log_expired": _lifecycle,
        "handler_error": _problem,
    }

    # --- asking ----------------------------------------------------------------

    def _shown(self, item: dict, guild: Optional[str]) -> bool:
        """Is this item part of one server's view? What belongs to no server (a start, say) is part of every view."""
        if guild is not None and item["guild"] not in (None, guild):
            return False
        # Her own messages are heard like anyone's; in the view they are the reply of a turn, not a message heard.
        return not (item["kind"] == "heard" and self.her_id and item.get("author") == self.her_id)

    def changed_since(self, after: int, guild: Optional[str]) -> list[dict]:
        """Items that changed after ``after`` events had been read, oldest first."""
        seen: set[str] = set()
        found = []
        for rev, item_id in reversed(self._changed):
            if rev <= after:
                break
            if item_id not in seen:
                seen.add(item_id)
                item = self.items.get(item_id)
                if item is not None and self._shown(item, guild):
                    found.append(item)
        return sorted(found, key=lambda item: item["at"])

    def page(self, guild: Optional[str], limit: int = 100, before: Optional[float] = None,
             kinds: Optional[Iterable[str]] = None, trouble: bool = False) -> list[dict]:
        """The newest items, newest first; with ``before``, those older than that moment."""
        wanted = set(kinds) if kinds else None
        found: list[dict] = []
        for item_id in reversed(self._order):
            item = self.items[item_id]
            if before is not None and item["at"] >= before:
                continue
            if not self._shown(item, guild) or (wanted is not None and item["kind"] not in wanted):
                continue
            if trouble and not self.in_trouble(item):
                continue
            found.append(item)
            if len(found) >= limit:
                break
        return found

    @staticmethod
    def level(item: dict) -> str:
        """How bad: "serious" for what failed outright, "hiccup" for what went wrong and was got over, "" for neither.

        A model call that fails once and works on the second try is a hiccup: the member only
        notices a slower reply. A reply that did not go out, a service that did not answer and an
        error are serious. By the owner's decision only the serious are counted.
        """
        if item["kind"] == "turn":
            if item["state"] in ("failed", "error") or any(
                    step.get("trouble") and step["what"] in SERIOUS_IN_A_TURN for step in item["steps"]):
                return "serious"
            return "hiccup" if item["problems"] else ""
        if item.get("trouble"):
            return "hiccup" if item.get("what") in NOTICES else "serious"
        return ""

    @classmethod
    def in_trouble(cls, item: dict) -> bool:
        return bool(cls.level(item))

    def running(self, guild: Optional[str] = None) -> list[dict]:
        """Reply turns in progress right now."""
        now = self._clock()
        return [item for item in (self.items[item_id] for item_id in self._open.values())
                if item["state"] == "running" and now - item["at"] < STALE_TURN
                and (guild is None or item["guild"] in (None, guild))]

    def busy(self) -> bool:
        return bool(self.running())

    def heard(self, message_id: str) -> Optional[dict]:
        return self.items.get("m:" + message_id)

    def turn(self, turn_id: str) -> Optional[dict]:
        return self.items.get("t:" + turn_id)

    def turn_of(self, message_id: str) -> Optional[dict]:
        heard = self.heard(message_id)
        return self.turn(heard["turn"]) if heard is not None and heard.get("turn") else None

    def held_back(self, guild: str) -> list[dict]:
        """The turns in which a member had her keep recent chat out of memory."""
        return [item for item in self.items.values()
                if item["kind"] == "turn" and item.get("held_back") and item["guild"] == guild]

    def batch_event(self, guild: str, channel: str, sent_at: float) -> Optional[dict]:
        """The log's record of the memory batch a channel sent at ``sent_at``: how long it took, what was dropped."""
        best = None
        for item in self._batches:
            if item["guild"] != guild or item["channel"] != channel:
                continue
            began = item["at"] - (item["fields"].get("ms") or 0) / 1000
            if abs(began - sent_at) <= 5 and (best is None or abs(began - sent_at) < abs(best[0] - sent_at)):
                best = (began, item)
        return best[1] if best else None

    def _since(self, since: float) -> Iterable[dict]:
        for item_id in reversed(self._order):
            item = self.items[item_id]
            if item["at"] < since:
                break  # items are kept in the order they began
            yield item

    def stats(self, guild: Optional[str], since: float) -> dict:
        """Counts for the pipeline strip: how much passed each stage since ``since``."""
        out = dict.fromkeys(("heard", "addressed", "turns", "flagged", "recalled", "calls", "tools", "sent", "failed",
                             "batches", "remembered", "pictures", "described", "problems", "hiccups", "dropped"), 0)
        took = []
        for item in self._since(since):
            if not self._shown(item, guild):
                continue
            kind = item["kind"]
            if kind == "heard":
                out["heard"] += 1
                out["addressed"] += 1 if item.get("addressed") else 0
            elif kind == "dropped":
                out["dropped"] += 1
            elif kind == "turn":
                out["turns"] += 1
                out["flagged"] += 1 if item.get("flagged") else 0
                out["recalled"] += 1 if item.get("facts") is not None else 0
                out["calls"] += item["calls"]
                out["tools"] += len(item["tools"])
                if item["state"] == "sent":
                    out["sent"] += 1
                    took.append(item.get("ms") or 0)
                elif item["state"] in ("failed", "error"):
                    out["failed"] += 1
            elif kind == "memory" and item.get("what") == "memory_batch":
                out["batches"] += 1
                out["remembered"] += item["fields"].get("messages") or 0
            elif kind == "picture":
                out["pictures"] += 1
                out["described"] += 1 if item.get("described") else 0
            level = self.level(item)
            if level:
                out["problems" if level == "serious" else "hiccups"] += 1
        out["median_ms"] = _middle(took)
        return out

    def health(self, guild: Optional[str], since: float) -> dict:
        """Timings for the health view: each reply, where its time went, the model calls, and what went wrong."""
        replies, problems, starts = [], [], []
        phases = dict.fromkeys(("check", "recall", "waiting", "reading", "writing", "tools", "rest"), 0.0)
        counted = 0
        for item in self._since(since):
            if not self._shown(item, guild):
                continue
            if item["kind"] == "system" and item.get("what") == "start":
                starts.append(item["at"])
            level = self.level(item)
            if level:
                problems.append({**item, "level": level})
            if item["kind"] != "turn" or item["state"] not in ("sent", "failed"):
                continue
            total = item.get("ms") or 0
            replies.append({"at": item["at"], "ms": total, "state": item["state"], "turn": item["turn"],
                            "tools": len(item["tools"]), "flagged": bool(item.get("flagged"))})
            spent = dict.fromkeys(phases, 0.0)
            for step in item["steps"]:
                what, ms = step["what"], step.get("ms") or 0
                if what == "security_check":
                    spent["check"] += ms
                elif what == "recall":
                    spent["recall"] += ms
                elif what == "tool_call":
                    spent["tools"] += ms
                elif what in ("model_call", "model_call_failed") and step.get("purpose") == "reply":
                    reading, writing = step.get("prompt_ms") or 0, step.get("predicted_ms") or 0
                    spent["reading"] += reading
                    spent["writing"] += writing
                    spent["waiting"] += max(0, ms - reading - writing)
            spent["rest"] = max(0.0, total - sum(spent.values()))
            for name, value in spent.items():
                phases[name] += value
            counted += 1
        calls: dict[str, dict] = {}
        for call in self.calls:
            # A call counts for the server it was made for; one the log ties to no server is left out.
            if call["at"] < since or (guild is not None and call["guild"] != guild):
                continue
            entry = calls.setdefault(str(call["purpose"]), {"ms": [], "wait": [], "prompt": [], "written": [], "failed": 0})
            if call["failed"]:
                entry["failed"] += 1
                continue
            entry["ms"].append(call["ms"])
            entry["wait"].append(max(0, call["ms"] - call["prompt_ms"] - call["predicted_ms"]))
            entry["prompt"].append(call["prompt_tokens"])
            entry["written"].append(call["completion_tokens"])
        return {
            "replies": sorted(replies, key=lambda reply: reply["at"]),
            "phases": {name: round(value / counted) for name, value in phases.items()} if counted else {},
            "calls": {purpose: {"count": len(entry["ms"]), "failed": entry["failed"], "median_ms": _middle(entry["ms"]),
                                "slow_ms": _high(entry["ms"]), "median_wait_ms": _middle(entry["wait"]),
                                "prompt_tokens": _middle(entry["prompt"]), "written_tokens": _middle(entry["written"])}
                      for purpose, entry in calls.items()},
            "problems": problems[:60],
            "starts": sorted(starts),
        }
