"""The monitor's web server: one page, and the answers it asks for.

It listens on this PC's own address only. Every request must name that address as its host, and
every request for data must carry the key handed out with the page, so that no other site open
in the browser can read the chat or press Start or Stop.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

from aiohttp import web

from . import files, health as health_checks, rooms
from .control import Control
from .eventlog import EventLog
from .health import Health
from .memory import OWN, Memory, Unavailable
from .pictures import Previews
from .settings import Settings

WEB = Path(__file__).resolve().parent / "web"
STATIC = {"app.css": "text/css", "icon.svg": "image/svg+xml",
          **{name + ".js": "text/javascript" for name in ("app", "core", "drawer", "live", "talk", "memory", "health")}}
STATIC_FILES = {name: WEB / name for name in STATIC}  # the only files the static route can serve
KEY_HEADER = "X-Monitor-Key"
PICTURES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
PICTURE_BYTES = 30 * 1024 * 1024
DAY = 86400
FIRST_PAGE = 150
EXCERPT = 320
SHOW = {"replies": {"turn"}, "heard": {"heard", "dropped"}, "memory": {"memory", "picture"}, "system": {"system"}}
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}
_DOCUMENT = re.compile(r"^\d+-\d+-\d+$")


def _cut(text: Optional[str], limit: int = EXCERPT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _fail(status: int, error: str) -> web.Response:
    return web.json_response({"error": error}, status=status)


def _memory_key(root: Path) -> str:
    """The memory service's key, from this program's environment, else the one line of the bot's .env
    that holds it. Nothing else in that file is read."""
    key = os.environ.get("HINDSIGHT_API_KEY", "").strip()
    if key:
        return key
    try:
        for line in (root / ".env").read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "HINDSIGHT_API_KEY":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


class Monitor:
    def __init__(self, settings: Settings, *, log: Optional[EventLog] = None, memory: Optional[Memory] = None,
                 health: Optional[Health] = None, control: Optional[Control] = None, token: Optional[str] = None,
                 clock: Callable[[], float] = time.time,
                 process: Callable[[Settings], dict] = health_checks.process):
        self.settings = settings
        self.log = log or EventLog(settings.log_dir, settings.history_days)
        self.memory = memory or Memory(settings.hindsight_url, api_key=_memory_key(settings.root))
        self.health = health or Health(settings)
        self.control = control or Control(settings, state=process)
        self.token = token or secrets.token_urlsafe(24)
        self._clock = clock
        self._process = process
        self.previews = Previews()
        self._names: dict[str, tuple[float, dict]] = {}  # server -> (when read, what her database names)

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._guard])
        app.router.add_get("/", self.page)
        app.router.add_get("/static/{name}", self.static)
        app.router.add_get("/api/poll", self.poll)
        app.router.add_get("/api/feed", self.feed)
        app.router.add_get("/api/channels", self.channels)
        app.router.add_get("/api/room", self.room)
        app.router.add_get("/api/told", self.told)
        app.router.add_get("/api/trace", self.trace)
        app.router.add_get("/api/memory", self.memory_overview)
        app.router.add_get("/api/memory/batch", self.memory_batch)
        app.router.add_get("/api/memory/facts", self.memory_facts)
        app.router.add_get("/api/health", self.health_view)
        app.router.add_get("/api/file", self.picture)
        app.router.add_post("/api/control", self.act)
        app.on_cleanup.append(self._cleanup)
        return app

    async def _cleanup(self, app: web.Application) -> None:
        await self.memory.close()
        await self.health.close()

    # --- who may ask -----------------------------------------------------------

    @web.middleware
    async def _guard(self, request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            response = await self._answer(request, handler)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text or exc.reason)
        for name, value in HEADERS.items():
            response.headers[name] = value
        return response

    async def _answer(self, request: web.Request, handler: Any) -> web.StreamResponse:
        sockname = request.transport.get_extra_info("sockname") if request.transport else None
        port = sockname[1] if sockname else self.settings.port
        # The host named must be this PC's own address: a page elsewhere that has had its own name
        # pointed at 127.0.0.1 would name itself here.
        if request.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return web.Response(status=403, text="Sanya Monitor answers on this PC only.")
        if request.path.startswith("/api/"):
            if not hmac.compare_digest(request.headers.get(KEY_HEADER, "").encode(), self.token.encode()):
                return _fail(403, "key")
            origin = request.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return _fail(403, "origin")
        try:
            return await handler(request)
        except sqlite3.Error:
            return _fail(503, "database")  # busy for a moment, or not there
        except Unavailable as exc:
            return _fail(503, f"memory service: {exc}")

    # --- the page --------------------------------------------------------------

    async def page(self, request: web.Request) -> web.Response:
        html = (WEB / "index.html").read_text(encoding="utf-8").replace("{{key}}", self.token)
        return web.Response(text=html, content_type="text/html")

    async def static(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in STATIC:
            raise web.HTTPNotFound()
        return web.Response(body=STATIC_FILES[name].read_bytes(), content_type=STATIC[name], charset="utf-8")

    # --- shared ----------------------------------------------------------------

    def _guild(self, request: web.Request) -> str:
        guild = request.query.get("guild", "")
        if not guild.isdigit():
            raise web.HTTPBadRequest(text="guild")
        return guild

    def _digits(self, request: web.Request, name: str) -> str:
        value = request.query.get(name, "")
        if not value.isdigit():
            raise web.HTTPBadRequest(text=name)
        return value

    def _open(self, guild: str) -> sqlite3.Connection:
        return files.open_database(rooms.database(self.settings.data_dir, guild))

    def _has_data(self, guild: str) -> bool:
        return rooms.database(self.settings.data_dir, guild).is_file()

    def _servers(self) -> list[dict]:
        settings = self.settings
        active = settings.active_servers
        known = list(dict.fromkeys([*(active or ()), *settings.banks, *rooms.guild_ids(settings.data_dir),
                                    *settings.servers]))
        servers = [{"id": guild, "active": active is None or guild in active, "bank": settings.banks.get(guild),
                    "data": self._has_data(guild)} for guild in known]
        # By the owner's choice only the servers she is switched on in are listed. If she is switched
        # on nowhere, all are, so that the page is not left empty.
        if not settings.show_switched_off and any(server["active"] for server in servers):
            servers = [server for server in servers if server["active"]]
        for server in servers:
            guild = server["id"]
            server["name"] = (settings.servers.get(guild) or (self._named(guild)["server"] if server["data"] else "")
                              or f"Server {guild[-4:]}")
        return sorted(servers, key=lambda server: (not server["active"], not server["data"], server["name"]))

    def _named(self, guild: str, conn: Optional[sqlite3.Connection] = None) -> dict:
        """What her database names for a server: its members, its channels and itself. Read at most every 15 seconds."""
        kept = self._names.get(guild)
        if kept is not None and self._clock() - kept[0] <= 15:
            return kept[1]
        try:
            if conn is not None:
                names = self._read_names(conn)
            else:
                with closing(self._open(guild)) as own:
                    names = self._read_names(own)
        except sqlite3.Error:
            names = kept[1] if kept is not None else {"members": {}, "channels": {}, "server": ""}
        self._names[guild] = (self._clock(), names)
        return names

    @staticmethod
    def _read_names(conn: sqlite3.Connection) -> dict:
        return {"members": rooms.names(conn), "channels": rooms.channel_names(conn), "server": rooms.server_name(conn)}

    def _status(self, guild: Optional[str]) -> dict:
        log = self.log
        process = self._process(self.settings)
        return {
            "process": process,
            "started_at": process.get("since") or (log.started_at if process["state"] != "stopped" else None),
            "connected_at": log.connected_at if log.connected_at >= log.started_at else None,
            "last_event": log.last_at or None,
            "now": self._clock(),
            # A turn the log still shows as open is not in progress if she is not running: she was stopped mid-reply.
            "replying": [{"turn": item["turn"], "channel": item["channel"], "at": item["at"], "message": item["message"]}
                         for item in log.running(guild)] if process["state"] != "stopped" else [],
            "servers": self._servers(),
            "control": {"busy": self.control.busy, "last": self.control.last},
            "rules": self._rules(),
        }

    def _rules(self) -> dict:
        """The bot's own settings that the page explains things by."""
        settings = self.settings
        return {"batch_messages": settings.batch_messages, "flush_minutes": settings.flush_minutes,
                "quiet_minutes": settings.quiet_minutes, "room_log_days": settings.room_log_days,
                "conversation_days": settings.conversation_days}

    async def _labels(self, guild: str, conn: Optional[sqlite3.Connection] = None) -> dict:
        """The names to show for channel and member ids."""
        named = self._named(guild, conn)
        channels: dict[str, str] = {}
        bank = self.settings.banks.get(guild)
        if bank:
            # For channels she has not named herself yet: the name she gave the memory service with a batch.
            channels.update(await self.memory.channel_names(bank))
        channels.update(named["channels"])
        channels.update(self.settings.channels)
        return {"channels": channels, "members": named["members"], "her": self.log.her_id}

    def _caught_up(self, row: dict) -> bool:
        """Was this message read from a channel's past when she started, and not as it was posted?

        Such a message is in the room log with no record of arriving. Either the log covers its
        time and has no such record, or she started within her catch-up reach after it.
        """
        if self.log.heard(row["id"]) is not None:
            return False
        reach = self.settings.catch_up_minutes * 60 + 60
        return (bool(self.log.first_at) and row["at"] >= self.log.first_at) or any(
            0 <= start - row["at"] <= reach for start in self.log.starts)

    def _kept_out(self, row: dict, held: list[dict]) -> str:
        """Why a message is kept out of memory, as far as the log can tell."""
        heard = self.log.heard(row["id"])
        if heard is None:
            return "caught_up" if self._caught_up(row) else "kept_out"
        if row["own"]:
            return "answer_kept_out" if row.get("reply_to") else "kept_out"
        turn = self.log.turn(heard["turn"]) if heard.get("turn") else None
        if turn is not None and turn.get("flagged"):
            return "flagged"
        if row["bot"]:
            return "bots_only"  # a batch in which only bots spoke: there was nothing to draw facts from
        # A member had her forget what they had just said (the owner may ask it for someone else).
        for asked in held:
            if row["at"] <= asked["at"] + 60 and asked.get("author") in (row["author"], self.settings.owner_id):
                return "held_back"
        return "kept_out"

    def _marks(self, row: dict, held: list[dict], answered: dict[str, list[dict]]) -> dict:
        """What became of a message: was it for her, was it answered, where does it stand with memory."""
        out: dict[str, Any] = {}
        heard = self.log.heard(row["id"])
        if heard is not None:
            out["via"], out["unanswered"] = heard.get("addressed"), heard.get("unanswered")
        turn = self.log.turn(heard["turn"]) if heard is not None and heard.get("turn") else None
        if turn is not None:
            out["turn"] = {"id": turn["turn"], "state": turn["state"], "ms": turn.get("ms"),
                           "flagged": bool(turn.get("flagged")), "problems": turn["problems"],
                           "level": EventLog.level(turn), "allowed": turn.get("allowed") or []}
        elif answered.get(row["id"]):
            out["answered"] = True
        if row["memory"] == "skip":
            out["kept_out"] = self._kept_out(row, held)
        return out

    @staticmethod
    def _bare(item: dict) -> dict:
        """A copy of a log item for the page. A turn's steps come with its trace; here only the last is named."""
        copy = dict(item)
        copy["level"] = EventLog.level(item)
        if item["kind"] == "turn":
            steps = item["steps"]
            copy["steps"] = len(steps)
            copy["last"] = {key: steps[-1].get(key) for key in ("what", "purpose", "tool_calls")} if steps else None
        return copy

    def _enrich(self, items: list[dict], guild: str) -> list[dict]:
        """Copies of log items with the words put to them: the log itself holds none."""
        wanted = {item["message"] for item in items if item["kind"] in ("heard", "turn", "dropped") and item.get("message")}
        pictures = [item for item in items if item["kind"] == "picture"]
        if not wanted and not pictures:
            return [self._bare(item) for item in items]
        try:
            with closing(self._open(guild)) as conn:
                said = rooms.messages(conn, wanted)
                saved = rooms.told(conn, wanted - set(said))
                answers = rooms.replies(conn, [item["message"] for item in items if item["kind"] == "turn"])
                loose = {}
                for item in items:
                    if item["kind"] == "turn" and item["message"] not in answers and item["channel"] \
                            and item["state"] != "running":
                        loose[item["id"]] = rooms.own_between(
                            conn, item["channel"], item["at"], (item.get("ended") or item["at"] + 600) + 3)
                kept = {}
                for item in pictures:
                    if item["guild"] == guild and isinstance(item.get("file"), int):
                        kept[item["id"]] = rooms.stored_file(conn, item["file"])
        except sqlite3.Error:
            return [self._bare(item) for item in items]
        shown = []
        for item in items:
            copy = self._bare(item)
            line = said.get(item.get("message") or "") or saved.get(item.get("message") or "")
            if item["kind"] in ("heard", "turn", "dropped") and line is not None:
                copy["name"], copy["text"] = line.get("name"), _cut(line.get("text"))
                copy["author"] = copy.get("author") or line.get("author")
                copy["attachments"] = len(line.get("attachments") or ())
                copy["memory"] = line.get("memory")
            if item["kind"] == "turn":
                theirs = answers.get(item["message"]) or loose.get(item["id"]) or []
                copy["reply"] = _cut(" ".join(one["text"] for one in theirs if one["text"]))
            if item["kind"] == "picture" and kept.get(item["id"]):
                stored = kept[item["id"]]
                copy.update(name=stored["name"], description=_cut(stored["description"]), message=stored["message"],
                            shows=stored["mime"] in PICTURES)
            shown.append(copy)
        return shown

    # --- live ------------------------------------------------------------------

    async def poll(self, request: web.Request) -> web.Response:
        """What changed since the page last asked, with how she is doing right now."""
        guild = request.query.get("guild", "")
        servers = self._servers()
        if guild not in {server["id"] for server in servers}:
            # The page's first look names no server: it is given the first one she is switched on in.
            guild = servers[0]["id"] if servers else "0"
        after = request.query.get("after")
        log = self.log
        log.refresh()
        if after is None or not after.isdigit() or int(after) < log.oldest_change or int(after) > log.seq:
            items, reset = list(reversed(log.page(guild, limit=FIRST_PAGE))), True
        else:
            items, reset = log.changed_since(int(after), guild), False
        return web.json_response({
            "guild": guild, "head": log.seq, "reset": reset, "items": self._enrich(items, guild),
            "status": self._status(guild),
            "stats": log.stats(guild, self._clock() - DAY), "labels": await self._labels(guild)})

    async def feed(self, request: web.Request) -> web.Response:
        """Older items, for scrolling back."""
        guild = self._guild(request)
        show = request.query.get("show", "")
        try:
            before = float(request.query["before"]) if "before" in request.query else None
        except ValueError:
            raise web.HTTPBadRequest(text="before") from None
        items = self.log.page(guild, limit=100, before=before, kinds=SHOW.get(show), trouble=show == "problems")
        return web.json_response({"items": self._enrich(list(reversed(items)), guild), "more": len(items) == 100})

    # --- conversations ---------------------------------------------------------

    async def channels(self, request: web.Request) -> web.Response:
        guild = self._guild(request)
        with closing(self._open(guild)) as conn:
            found = rooms.channels(conn)
            labels = await self._labels(guild, conn)
        return web.json_response({"channels": found, "labels": labels})

    async def room(self, request: web.Request) -> web.Response:
        """Everything she heard in a channel, newest first, each message with what became of it."""
        guild, channel = self._guild(request), self._digits(request, "channel")
        only, q = request.query.get("only", ""), request.query.get("q", "")[:100]
        try:
            before = float(request.query["before"]) if "before" in request.query else None
        except ValueError:
            raise web.HTTPBadRequest(text="before") from None
        self.log.refresh()
        with closing(self._open(guild)) as conn:
            found = rooms.room(conn, channel, before=before, limit=rooms.PAGE, only=only, q=q)
            ids = [row["id"] for row in found]
            kept = rooms.stored_files(conn, ids)
            answered = rooms.replies(conn, ids)
            labels = await self._labels(guild, conn)
        held = self.log.held_back(guild)
        for row in found:
            row["files"] = [{**one, "shows": one["mime"] in PICTURES} for one in kept.get(row["id"], [])]
            row.update(self._marks(row, held, answered))
        return web.json_response({"messages": found, "more": len(found) == rooms.PAGE, "labels": labels,
                                  "rules": self._rules()})

    async def told(self, request: web.Request) -> web.Response:
        """A channel's conversation as she is given it."""
        guild, channel = self._guild(request), self._digits(request, "channel")
        before = request.query.get("before")
        with closing(self._open(guild)) as conn:
            found = rooms.history(conn, channel, before=int(before) if before and before.isdigit() else None)
            labels = await self._labels(guild, conn)
        return web.json_response({"entries": found, "more": len(found) == rooms.PAGE, "labels": labels})

    async def trace(self, request: web.Request) -> web.Response:
        """One message, followed from arriving to memory."""
        guild, message_id = self._guild(request), self._digits(request, "message")
        self.log.refresh()
        log = self.log
        heard, turn = log.heard(message_id), log.turn_of(message_id)
        with closing(self._open(guild)) as conn:
            found = rooms.messages(conn, [message_id]).get(message_id)
            asked: list[dict] = []
            if turn is not None:
                anchor = rooms.messages(conn, [turn["message"]]).get(turn["message"])
                if anchor is not None:
                    asked = rooms.asked(conn, anchor["channel"], anchor["author"], anchor["at"], turn["messages"])
                hers = rooms.replies(conn, [turn["message"]]).get(turn["message"], [])
                if not hers and turn["channel"] and turn["state"] != "running":
                    hers = rooms.own_between(conn, turn["channel"], turn["at"],
                                             (turn.get("ended") or turn["at"] + 600) + 3)
            else:
                hers = rooms.replies(conn, [message_id]).get(message_id, [])
            ids = {message_id, *(one["id"] for one in asked), *(one["id"] for one in hers)}
            had = rooms.recalled(conn, turn["message"] if turn is not None else message_id)
            kept = rooms.stored_files(conn, ids)
            saved = rooms.told(conn, ids)
            batch = rooms.batch(conn, found["document"]) if found is not None and found["document"] else None
            labels = await self._labels(guild, conn)
        if found is None and message_id not in saved and heard is None:
            return _fail(404, "message")
        memory: dict[str, Any] = {"state": found["memory"] if found is not None else None}
        if found is not None and found["memory"] == "skip":
            memory["kept_out"] = self._kept_out(found, log.held_back(guild))
        if batch is not None:
            event = log.batch_event(guild, batch["channel"], batch["at"])
            memory["batch"] = {**batch, **({"took_ms": event["fields"].get("ms"), "dropped": event["fields"].get("dropped")}
                                           if event else {})}
        return web.json_response({
            "message": found, "heard": heard, "turn": turn, "asked": asked, "replies": hers,
            "files": {key: [{**one, "shows": one["mime"] in PICTURES} for one in value] for key, value in kept.items()},
            "saved": saved, "memory": memory, "labels": labels, "rules": self._rules(), "recalled": had,
            "turn_level": EventLog.level(turn) if turn is not None else "",
            "caught_up": found is not None and self._caught_up(found)})

    # --- memory ----------------------------------------------------------------

    def _bank(self, guild: str) -> Optional[str]:
        return self.settings.banks.get(guild)

    async def memory_overview(self, request: web.Request) -> web.Response:
        guild = self._guild(request)
        self.log.refresh()
        with closing(self._open(guild)) as conn:
            sent = rooms.batches(conn, 200)
            waiting = rooms.waiting(conn)
            removals = rooms.removals(conn)
            labels = await self._labels(guild, conn)
        for batch in sent:
            event = self.log.batch_event(guild, batch["channel"], batch["at"])
            if event is not None:
                batch.update(took_ms=event["fields"].get("ms"), dropped=event["fields"].get("dropped"))
        bank, stats, error = self._bank(guild), None, None
        if bank:
            try:
                stats = await self.memory.stats(bank)
            except Unavailable as exc:
                error = str(exc)
        return web.json_response({"bank": bank, "stats": stats, "error": error, "batches": sent, "waiting": waiting,
                                  "removals": len(removals), "labels": labels, "rules": self._rules()})

    async def memory_batch(self, request: web.Request) -> web.Response:
        """One batch: the chat that went, and the facts the service drew from it."""
        guild, document = self._guild(request), request.query.get("document", "")
        if not _DOCUMENT.match(document) or not document.startswith(guild + "-"):
            raise web.HTTPBadRequest(text="document")
        with closing(self._open(guild)) as conn:
            batch = rooms.batch(conn, document)
            said = rooms.batch_messages(conn, document)
            labels = await self._labels(guild, conn)
        if batch is None:
            return _fail(404, "batch")
        event = self.log.batch_event(guild, batch["channel"], batch["at"])
        if event is not None:
            batch.update(took_ms=event["fields"].get("ms"), dropped=event["fields"].get("dropped"))
        out: dict[str, Any] = {"batch": batch, "messages": said, "labels": labels, "error": None}
        bank = self._bank(guild)
        if bank:
            try:
                out["people"] = await self.memory.drawn(bank, document)
                out["own"] = await self.memory.drawn(bank, document + OWN)
                out["documents"] = {"people": await self.memory.document(bank, document),
                                    "own": await self.memory.document(bank, document + OWN)}
            except Unavailable as exc:
                out["error"] = str(exc)
        return web.json_response(out)

    async def memory_facts(self, request: web.Request) -> web.Response:
        """The facts in this server's bank, searched by a word."""
        guild = self._guild(request)
        bank = self._bank(guild)
        if not bank:
            return _fail(404, "no memory bank for this server")
        state, kind = request.query.get("state", ""), request.query.get("kind", "")
        if state not in ("", "invalidated") or kind not in ("", "observation"):
            raise web.HTTPBadRequest(text="filter")
        offset = request.query.get("offset", "0")
        q, offset = request.query.get("q", "")[:100], int(offset) if offset.isdigit() else 0
        if kind == "observation":  # the merged summaries, which she is never shown
            found = await self.memory.facts(bank, q=q, state=state, kind=kind, limit=50, offset=offset)
        else:
            found = await self.memory.facts_only(bank, q=q, state=state, limit=50, offset=offset)
        return web.json_response(found)

    # --- health ----------------------------------------------------------------

    async def health_view(self, request: web.Request) -> web.Response:
        guild = self._guild(request)
        hours = request.query.get("hours", "24")
        hours = min(int(hours), 24 * 30) if hours.isdigit() and int(hours) > 0 else 24
        self.log.refresh()
        status = self._status(guild)
        database = None
        try:
            with closing(self._open(guild)) as conn:
                database = rooms.counts(conn)
        except sqlite3.Error:
            pass
        out = {
            "status": status, "hours": hours, "database": database,
            "services": await self.health.services(),
            "usage": self.health.usage(busy=self.log.busy() and status["process"]["state"] != "stopped"),
            "storage": await self.health.storage(),
            "log": self.log.health(guild, self._clock() - hours * 3600),
            "console": self.health.console() if status["process"]["state"] == "stopped" else "",
        }
        return web.json_response(out)

    # --- pictures --------------------------------------------------------------

    async def picture(self, request: web.Request) -> web.Response:
        """A picture she kept, for showing beside its message. Only real picture types are sent."""
        guild, file_id = self._guild(request), self._digits(request, "id")
        with closing(self._open(guild)) as conn:
            stored = rooms.stored_file(conn, int(file_id))
        if stored is None or stored["mime"] not in PICTURES:
            raise web.HTTPNotFound()
        root = (self.settings.data_dir / "files").resolve()
        path = (root / stored["path"]).resolve()
        if root not in path.parents or files.size(path) > PICTURE_BYTES:
            raise web.HTTPNotFound()
        small = request.query.get("size") == "small"
        key = (guild, int(file_id))
        data = b"" if small and self.previews.has(key) else files.read_bytes(path)
        if small:
            # Shrunk once, off the main thread, and held in memory: a row of previews must not mean megabytes each.
            preview = await asyncio.get_running_loop().run_in_executor(None, self.previews.of, key, data)
            if preview is None:
                raise web.HTTPNotFound()
            return web.Response(body=preview, content_type="image/jpeg")
        if not data:
            raise web.HTTPNotFound()  # the owner has cleared it away
        return web.Response(body=data, content_type=stored["mime"])

    # --- start and stop --------------------------------------------------------

    async def act(self, request: web.Request) -> web.Response:
        try:
            asked = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="json") from None
        done = await self.control.act(str(asked.get("action") if isinstance(asked, dict) else ""))
        self.log.refresh()
        return web.json_response({**done, "process": self._process(self.settings)})
