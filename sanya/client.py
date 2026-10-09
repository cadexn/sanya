"""The Discord connection.

Every admitted message goes into the room log, whoever it was for. A message
addressed to her is also queued for a reply turn; without a replier she only
listens.
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import discord

from . import events
from .config import Config
from .intake import addressing
from .intake.admission import CHAT_KINDS, Admission, Incoming
from .intake.attachments import FileStore
from .intake.batching import Batcher
from .intake.bots import BotExchanges
from .memory.ambient import Ambient, forget_messages
from .persona import time_of_day
from .store import history, ledger, recalled, room_log
from .store.db import Store
from .turn import Addressed, Replier

_KINDS = {discord.MessageType.default: "default", discord.MessageType.reply: "reply"}
EXPIRY_EVERY = 3600  # seconds between sweeps of a server's room log


def build_intents() -> discord.Intents:
    """Server messages and their text. No direct messages, so those never arrive."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    # Without this the library keeps each server's emoji and sticker lists empty, and she can use
    # neither the server's own emoji nor its stickers. It is not a privileged intent.
    intents.emojis_and_stickers = True
    return intents


def to_incoming(message: discord.Message, reply_to_author_id: Optional[str] = None) -> Incoming:
    guild = getattr(message, "guild", None)
    author = message.author
    parent_id = getattr(message.channel, "parent_id", None)  # set on threads only
    if reply_to_author_id is None:
        replied = getattr(getattr(getattr(message, "reference", None), "resolved", None), "author", None)
        reply_to_author_id = str(replied.id) if replied is not None else None
    return Incoming(
        message_id=str(message.id),
        guild_id=str(guild.id) if guild is not None else None,
        channel_id=str(message.channel.id),
        author_id=str(author.id),
        author_is_bot=bool(getattr(author, "bot", False)),
        kind=_KINDS.get(message.type, "other"),
        text=message.content or "",
        author_name=getattr(author, "display_name", None) or getattr(author, "name", None) or "",
        mentioned_ids=tuple(str(user.id) for user in getattr(message, "mentions", None) or ()),
        reply_to_author_id=reply_to_author_id,
        reply_to_message_id=(str(reference_id) if (reference_id := getattr(getattr(message, "reference", None),
                                                                           "message_id", None)) is not None else None),
        readable_text=getattr(message, "clean_content", None) or message.content or "",
        attachment_count=len(getattr(message, "attachments", None) or ()),
        parent_channel_id=str(parent_id) if parent_id is not None else None,
    )


def log_text(message: Any, readable: str, embed_chars: int) -> str:
    """What the room log keeps of a message: its text, and a note of any embed or sticker."""
    parts = [readable]
    for embed in getattr(message, "embeds", None) or ():
        words = " - ".join(str(value) for value in (getattr(embed, "title", None), getattr(embed, "description", None))
                           if value)
        if words:
            parts.append(f"[embed: {' '.join(words.split())[:embed_chars]}]")
    for sticker in getattr(message, "stickers", None) or ():
        parts.append(f"[sticker: {getattr(sticker, 'name', 'sticker')}]")
    return " ".join(part for part in parts if part).strip()


class SanyaClient(discord.Client):
    def __init__(self, config: Config, store: Store, replier: Optional[Replier] = None,
                 exchanges: Optional[BotExchanges] = None, files: Optional[FileStore] = None,
                 ambient: Optional[Ambient] = None, statuses: Optional[dict[str, tuple[str, ...]]] = None):
        super().__init__(intents=build_intents(), allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.store = store
        self.admission = Admission(config.discord.active_servers)
        self.replier = replier
        self.exchanges = exchanges or BotExchanges(config.discord.bots_as_people, config.intake.bot_exchange_ceiling)
        self.files = files
        self.ambient = ambient
        self.statuses = statuses or {}
        self._ambient_running = False
        self._status_running = False
        # Bot accounts recorded as people: what they say can become facts, and they are exempt from the bot rule.
        self._people = set(config.discord.bots_as_people)
        self.batcher = None if replier is None else Batcher(
            config.intake.batch_wait_seconds, config.intake.max_turn_restarts, replier.run)
        self._background: set[asyncio.Future] = set()  # downloads and catch-up, kept so they are not lost
        self._swept: dict[str, float] = {}  # server -> when its room log was last swept

    def _spawn(self, coroutine: Any, where: str) -> None:
        async def guarded() -> None:
            try:
                await coroutine
            except Exception as exc:  # background work must not take the connection down
                events.log_event("handler_error", where=where, error=type(exc).__name__)

        task = asyncio.ensure_future(guarded())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _active(self, guild_id: Any) -> bool:
        active = self.config.discord.active_servers
        return guild_id is not None and (active is None or str(guild_id) in active)

    async def on_ready(self) -> None:
        events.log_event("connected", user_id=str(self.user.id), guilds=len(self.guilds))
        for guild in self.guilds:
            guild_id = str(guild.id)
            if not self._active(guild_id):
                events.log_event("guild_inactive", guild_id=guild_id)
                continue
            conn = self.store.guild(guild_id)
            if isinstance(getattr(guild, "name", None), str) and guild.name:
                recalled.safely("server_name", lambda: room_log.note_server(conn, guild.name))
            events.log_event("guild_ready", guild_id=guild_id)
            self._spawn(self._catch_up(guild), "catch_up")
        if self.ambient is not None and not self._ambient_running:
            # on_ready comes again after a reconnect; the background worker is started once.
            self._ambient_running = True
            self._spawn(self.ambient.run(), "ambient")
        if self.statuses and not self._status_running:
            self._status_running = True
            self._spawn(self._keep_status(), "status")

    async def _keep_status(self) -> None:
        """Show a line under her name that fits the time of day, and change it every few hours."""
        while True:
            lines = self.statuses.get(time_of_day(datetime.now().hour))
            if lines:
                try:
                    await self.change_presence(activity=discord.CustomActivity(name=random.choice(lines)))
                    events.log_event("status_changed")
                except discord.HTTPException as exc:
                    events.log_event("handler_error", where="status", error=type(exc).__name__)
            await asyncio.sleep(self.config.discord.status_hours * 3600)

    # --- the room log ----------------------------------------------------------

    def _log(self, message: Any, incoming: Incoming, *, own: bool, addressed: bool, memory: str = "pending") -> bool:
        conn = self.store.guild(incoming.guild_id)
        created = getattr(message, "created_at", None)
        now = time.time()
        is_bot = incoming.author_is_bot and incoming.author_id not in self._people
        at = created.timestamp() if created is not None else now
        reply_to = incoming.reply_to_message_id or ""
        if own:
            # The second message of a long reply answers what the first did. And her answer to a
            # message that is kept out of memory (a flagged one, or one a member asked her to
            # forget) is kept out with it: it repeats what was said.
            reply_to = reply_to or room_log.continued(conn, incoming.channel_id, at)
            if reply_to and room_log.answers_kept_out(conn, reply_to):
                memory = "skip"
        line = room_log.Line(
            incoming.message_id, incoming.channel_id, incoming.author_id, incoming.author_name,
            is_bot, log_text(message, incoming.readable_text, self.config.room_log.embed_chars), at,
            tuple(str(getattr(one, "filename", "") or "file") for one in getattr(message, "attachments", None) or ()),
            own, addressed, reply_to)
        added = room_log.record(conn, line, memory=memory)
        if not is_bot and incoming.author_name:
            room_log.note_name(conn, incoming.author_id, incoming.author_name, now)
        channel = getattr(getattr(message, "channel", None), "name", None)
        if isinstance(channel, str) and channel:
            recalled.safely("channel_name", lambda: room_log.note_channel(conn, incoming.channel_id, channel, now))
        if now - self._swept.get(incoming.guild_id, 0) > EXPIRY_EVERY:
            self._swept[incoming.guild_id] = now
            gone = room_log.expire(conn, now - self.config.room_log.keep_days * 86400)
            ledger.expire_images(conn, now - self.config.media.ledger_keep_days * 86400)
            if gone:
                events.log_event("room_log_expired", guild_id=incoming.guild_id, rows=gone)
        return added

    def _overhear(self, message: Any, incoming: Incoming) -> None:
        """Keep the pictures of a message that was not for her, in the background."""
        attachments = getattr(message, "attachments", None) or ()
        if self.files is not None and attachments:
            self._spawn(self.files.take(incoming.guild_id, incoming.channel_id, incoming.message_id,
                                        incoming.author_id, attachments, kind="overheard", images_only=True),
                        "overhear")

    async def _catch_up(self, guild: Any) -> None:
        """After a start, read each channel's recent chat into the room log.

        It gives her context for her first replies. Caught-up chat is never
        offered to memory, and a ping in it is not answered.
        """
        window = self.config.context
        since = discord.utils.utcnow() - timedelta(minutes=window.minutes)
        self_id = str(self.user.id) if self.user else ""
        added = 0
        for channel in [*getattr(guild, "text_channels", ()), *getattr(guild, "threads", ())]:
            try:
                async for message in channel.history(limit=window.max_messages, after=since, oldest_first=True):
                    incoming = to_incoming(message)
                    if incoming.kind not in CHAT_KINDS:
                        continue
                    own = incoming.author_id == self_id
                    if self._log(message, incoming, own=own, addressed=False, memory="skip"):
                        added += 1
                        if not own:
                            self._overhear(message, incoming)
            except discord.HTTPException:
                continue  # a channel she cannot read
        events.log_event("caught_up", guild_id=str(guild.id), messages=added)

    def _deleted(self, guild_id: Any, channel_id: Any, message_ids: Iterable[Any]) -> None:
        """A deleted message leaves the room log, the conversation and her reach."""
        if not self._active(guild_id):
            return
        ids = {str(message_id) for message_id in message_ids}
        conn = self.store.guild(str(guild_id))
        # Facts already drawn from these messages are taken back by the background worker.
        batches = forget_messages(conn, sorted(ids))
        logged = sum(room_log.delete(conn, message_id) for message_id in ids)
        for message_id in ids:
            history.delete_message(conn, message_id)
            # What she had from memory to answer it goes too.
            recalled.safely("recalled", lambda: recalled.delete_message(conn, message_id))
            # The file stays on disk for the owner; she can no longer open it.
            conn.execute("DELETE FROM files WHERE message_id = ?", (message_id,))
        waiting = 0
        if self.batcher is not None:
            waiting = self.batcher.discard(str(channel_id), lambda item: item.incoming.message_id in ids)
        events.log_event("message_deleted", guild_id=str(guild_id), channel_id=str(channel_id),
                         messages=len(ids), logged=logged, waiting=waiting, batches=batches)

    async def on_raw_message_delete(self, payload: Any) -> None:
        try:
            self._deleted(payload.guild_id, payload.channel_id, [payload.message_id])
        except Exception as exc:
            events.log_event("handler_error", where="on_message_delete", error=type(exc).__name__)

    async def on_raw_bulk_message_delete(self, payload: Any) -> None:
        try:
            self._deleted(payload.guild_id, payload.channel_id, payload.message_ids)
        except Exception as exc:
            events.log_event("handler_error", where="on_message_delete", error=type(exc).__name__)

    # --- messages --------------------------------------------------------------

    async def _referenced(self, message: discord.Message) -> Optional[discord.Message]:
        """The message this one replies to, fetched if Discord did not send it along."""
        reference = getattr(message, "reference", None)
        if reference is None:
            return None
        resolved = reference.resolved
        if resolved is not None:
            # A deleted message arrives as a stub with no author.
            return resolved if getattr(resolved, "author", None) is not None else None
        if reference.message_id is None:
            return None
        try:
            return await message.channel.fetch_message(reference.message_id)
        except discord.HTTPException:
            return None

    async def on_message(self, message: discord.Message) -> None:
        try:
            referenced = await self._referenced(message)
            incoming = to_incoming(message, str(referenced.author.id) if referenced is not None else None)
            decision = self.admission.check(incoming)
            events.log_event(
                "message_admitted" if decision.admitted else "message_dropped",
                reason=decision.reason,
                guild_id=incoming.guild_id,
                channel_id=incoming.channel_id,
                message_id=incoming.message_id,
                author_id=incoming.author_id,
                author_is_bot=incoming.author_is_bot,
            )
            if not decision.admitted:
                return
            if not incoming.author_is_bot:
                self.exchanges.person_spoke(incoming.channel_id)
            self_id = str(self.user.id) if self.user else ""
            verdict = addressing.check(incoming, self_id=self_id,
                                       no_reply_channels=self.config.discord.no_reply_channels)
            if (verdict.addressed and incoming.author_is_bot
                    and not self.exchanges.may_answer(incoming.channel_id, incoming.author_id)):
                verdict = addressing.Addressing(False, "bot_ceiling")
            own = verdict.reason == "own_message"
            queued = verdict.addressed and self.batcher is not None
            self._log(message, incoming, own=own, addressed=queued)
            if not queued and not own:
                self._overhear(message, incoming)
            if verdict.reason in ("own_message", "not_addressed"):
                return
            events.log_event("message_addressed" if verdict.addressed else "message_not_answered",
                             reason=verdict.reason, channel_id=incoming.channel_id,
                             message_id=incoming.message_id)
            if queued:
                if self.ambient is not None:
                    self.ambient.touch()  # background memory work waits while she is being talked to
                # She is shown a replied-to message only when it is someone else's; her own is in the conversation.
                theirs = referenced is not None and str(referenced.author.id) != self_id
                self.batcher.add(incoming.channel_id, incoming.author_id,
                                 Addressed(incoming, message, referenced if theirs else None))
        except Exception as exc:  # one bad event must not stop the connection
            events.log_event("handler_error", where="on_message", error=type(exc).__name__)

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()
        if self.batcher is not None:
            await self.batcher.close()
            await self.replier.close()
        await super().close()
