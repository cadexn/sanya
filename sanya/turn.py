"""One reply turn: from a batch of messages addressed to her to the reply in the channel."""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from . import events
from .config import Config
from .conversation import context as room
from .conversation import media as media_decision
from .conversation import prompt
from .conversation.prompt import captioned
from .conversation.window import window
from .intake.admission import Incoming
from .intake.attachments import FileStore, describe, describe_refused
from .intake.batching import Turn
from .intake.bots import BotExchanges
from .intake.security import SecurityCheck
from .memory import recall
from .memory.hindsight import Hindsight
from .model import loop
from .model.client import ModelClient, ModelFailure
from .outbox import egress
from .outbox.send import TriggerGone, send_image, send_reply, split_reply
from .persona import Persona
from .store import history, ledger, recalled, room_log
from .store.db import Store
from .store.history import Entry
from .tools.media import STICKER_LEDGER, Media, MediaTurn
from .tools.toolbox import ReactTurn, Toolbox, ToolContext

DAY = 86400
UPLOAD_LIMIT = 10 * 1024 * 1024  # what Discord allows a server with no boosts
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)")  # an image link she wrote herself, which is never posted
_ENDING = {"image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_POSTED = re.compile(r"(?m)^you (?:posted a|sent a sticker)")  # her note on a turn in which an image or a sticker went out
BYPASS_TAG = "[told to ignore your rules]"  # the note on a turn whose message tells her to drop her rules
FOLLOW_UP = 6  # how many entries of the conversation back "another one" still refers to an image she posted
# What the event log says of a turn that may carry neither an image nor a reaction: no dice were thrown.
NO_EXTRAS = {"image_mode": "off", "image_roll": None, "image_cooldown": False, "no_media": False,
             "reaction_allowed": False, "reaction_roll": None, "reaction_short": False, "reaction_cooldown": False}


@dataclass(frozen=True)
class Addressed:
    """A message addressed to her, with the Discord objects needed to answer it."""

    incoming: Incoming
    message: Any  # discord.Message
    replied: Any = None  # the message it replies to, when that is someone else's


class Replier:
    def __init__(self, config: Config, store: Store, persona: Persona, model: ModelClient,
                 security: SecurityCheck, exchanges: BotExchanges, files: FileStore, toolbox: Toolbox,
                 secrets: Iterable[str] = (), clock: Callable[[], float] = time.time,
                 hindsight: Optional[Hindsight] = None, on_activity: Callable[[], None] = lambda: None,
                 media: Optional[Media] = None, roll: Callable[[], float] = random.random):
        self._config = config
        self._store = store
        self._persona = persona
        self._model = model
        self._security = security
        self._exchanges = exchanges
        self._files = files
        self._toolbox = toolbox
        self._secrets = tuple(secrets)  # values that must never be posted
        self._clock = clock
        self._hindsight = hindsight
        self._on_activity = on_activity  # tells the background worker she is busy
        self._media = media
        self._roll = roll  # the dice for spontaneous images and reactions

    async def _gather(self, item: Addressed, guild_id: str, channel_id: str) -> tuple[str, list, str]:
        """What one message says, with notes about its files; the pictures to show with it; and the
        part of it a person wrote, which is what the security check screens."""
        incoming = item.incoming
        text = (incoming.readable_text or incoming.text).strip()
        shown: list[tuple[str, str]] = []
        written = text  # the bot's own notes about files are not the member's words
        replied = item.replied
        if replied is not None:
            # She is shown the message being replied to: who wrote it, what it said, its pictures.
            author = getattr(replied, "author", None)
            name = prompt.speaker_name(getattr(author, "display_name", None) or getattr(author, "name", None))
            quoted = " ".join(str(getattr(replied, "clean_content", None) or getattr(replied, "content", "")
                                  or "").split())[:self._config.context.quote_chars]
            kept, _ = await self._files.take(guild_id, channel_id, str(getattr(replied, "id", "")),
                                             str(getattr(author, "id", "")), getattr(replied, "attachments", None) or (),
                                             kind="overheard", images_only=True)
            notes = "".join(" " + describe(stored) for stored in kept)
            text = f'[replying to {name}, who wrote: "{quoted}"{notes}] {text}'.strip()
            written = f"{quoted}\n{written}".strip()
            for stored in kept:
                shown.extend(captioned(f"[file {stored.file_id}: a picture in the message being replied to]",
                                       self._files.pictures(stored)))
        kept, refused = await self._files.take(guild_id, channel_id, incoming.message_id, incoming.author_id,
                                               getattr(item.message, "attachments", None) or ())
        for stored in kept:
            text = f"{text} {describe(stored)}".strip()
            if stored.is_image:
                shown.extend(captioned(f"[file {stored.file_id}: a picture attached to the message said to you]",
                                       self._files.pictures(stored)))
        for one in refused:
            text = f"{text} {describe_refused(one, self._config.intake.attachments)}".strip()
        return text, shown, written

    def _extras(self, conn: Any, channel_id: str, trigger: Any, author_id: str, texts: list[str],
                now: float, just_posted: bool = False) -> tuple[Optional[MediaTurn], Optional[ReactTurn], dict]:
        """Whether this turn may carry an image, and whether she may react with an emoji.

        The third value is what was decided and on what dice, for the event log: modes, rolls and
        flags, never anything that was said.
        """
        for text in texts:
            ledger.set_no_media(conn, author_id, media_decision.wish(text), now)
        settings = self._config.media
        chosen = dict(NO_EXTRAS, no_media=ledger.wants_no_media(conn, author_id))
        image = None
        if self._media is not None and self._media.available:
            roll = self._roll()
            cooling_down = now - ledger.last_spontaneous(conn, channel_id) < settings.cooldown_minutes * 60
            mode = media_decision.decide(texts, roll=roll, chance=settings.spontaneous_chance,
                                         cooling_down=cooling_down, no_media=chosen["no_media"],
                                         just_posted=just_posted)
            chosen.update(image_mode=mode, image_roll=round(roll, 2), image_cooldown=cooling_down)
            if mode != "off":
                # A requested image ignores the search budget; the six-call limit still bounds it.
                asked = mode == "requested"
                image = MediaTurn(
                    mode, self._config.model.max_calls_per_reply if asked else settings.optional_searches,
                    settings.requested_candidates if asked else settings.optional_candidates,
                    min(getattr(trigger.guild, "filesize_limit", UPLOAD_LIMIT),
                        self._config.intake.attachments.max_mb * 1024 * 1024),
                    meme=asked and any(media_decision.asked_for_meme(text) for text in texts),
                    max_images=settings.requested_images if asked else 1,
                    # The server's own stickers go where an image may; unasked, less any sent here in the last day.
                    stickers=self._stickers(conn, trigger, now, asked),
                    asked_sticker=asked and any(media_decision.asked_for_sticker(text) for text in texts))
                if image.meme:
                    chosen["image_mode"] = "requested_meme"
        emoji = self._config.emoji
        reaction = None
        short = len(texts[-1]) <= emoji.short_chars
        cooling_down = now - ledger.last_reaction(conn, channel_id) < emoji.cooldown_minutes * 60
        roll = self._roll() if short else None  # the dice are thrown only for a short message
        if roll is not None and roll < emoji.chance and not cooling_down:
            reaction = ReactTurn({one.name: one for one in getattr(trigger.guild, "emojis", None) or ()})
        chosen.update(reaction_allowed=reaction is not None, reaction_roll=None if roll is None else round(roll, 2),
                      reaction_short=short, reaction_cooldown=cooling_down)
        return image, reaction, chosen

    def _stickers(self, conn: Any, trigger: Any, now: float, asked: bool) -> dict[str, Any]:
        """The server's own stickers she may send now, by lower-cased name. Discord lets a bot send no others.

        Unasked, the same sticker is not sent twice within the repeat limit, like an image. When
        someone asks for one (``asked``) there is no such limit.
        """
        day_ago = now - self._config.media.repeat_hours * 3600
        usable: dict[str, Any] = {}
        for one in getattr(trigger.guild, "stickers", None) or ():
            name = " ".join(str(getattr(one, "name", "") or "").split()).casefold()
            if (name and name not in usable and getattr(one, "available", True)
                    and (asked or not ledger.posted_since(conn, STICKER_LEDGER.format(id=one.id), day_ago))):
                usable[name] = one
        return usable

    async def run(self, channel_id: str, items: list[Addressed], turn: Turn) -> None:
        trigger = items[-1].message
        last = items[-1].incoming
        guild_id = last.guild_id
        conn = self._store.guild(guild_id)
        started = self._clock()
        events.turn_id.set(f"{last.message_id}-{turn.restarts}")
        events.log_event("turn_started", guild_id=guild_id, channel_id=channel_id, message_id=last.message_id,
                         messages=len(items), restarts=turn.restarts)
        self._on_activity()

        async with trigger.channel.typing():
            texts, pictures, written = [], [], []
            for item in items:
                text, shown, words = await self._gather(item, guild_id, channel_id)
                texts.append(text)
                pictures.extend(shown)
                written.append(words)
            verdict = await self._security.check(written)
            events.log_event("security_check", channel_id=channel_id, flagged=verdict.flagged,
                             reason=verdict.reason, ms=round((self._clock() - started) * 1000))
            # A flagged turn gets no memory tools and no memory writes, and its messages carry
            # the untrusted mark while they are being answered. It keeps her other tools.
            if verdict.flagged:
                room_log.skip_memory(conn, [item.incoming.message_id for item in items])
            said = [Entry(channel_id, "user", text, started, item.incoming.author_id, item.incoming.author_name,
                          item.incoming.message_id, verdict.flagged) for item, text in zip(items, texts)]
            limit = self._config.conversation.window_messages
            entries = window(history.recent(conn, channel_id, limit) + said, limit)
            # What was said around her lately, and the newest pictures posted there.
            settings = self._config.context
            around = room.build(conn, self._files, guild_id, channel_id, now=started, minutes=settings.minutes,
                                max_messages=settings.max_messages, pictures=settings.pictures,
                                line_chars=settings.line_chars)
            # Did she post an image or a sticker in the last few exchanges here? Then "another one" asks for one.
            just_posted = any(_POSTED.search(entry.notes or "") for entry in entries[:len(entries) - min(len(said), len(entries))][-FOLLOW_UP:])
            # A message that tells her to ignore her rules gets no tools at all, so no image and no reaction
            # either, and a note that she is to do none of what it asks (the owner's decision of 9 October 2026).
            image, reaction, chosen = (None, None, NO_EXTRAS) if verdict.bypass else self._extras(
                conn, channel_id, trigger, last.author_id, texts, started, just_posted)
            # What this turn was allowed, and on what dice. Modes, rolls and counts: nothing that was said.
            events.log_event("turn_choices", guild_id=guild_id, channel_id=channel_id, **chosen,
                             tools=not verdict.bypass, memory_tools=not verdict.flagged, quote=any(item.replied is not None for item in items),
                             attachments=sum(len(getattr(item.message, "attachments", None) or ()) for item in items))
            tags = ([BYPASS_TAG] if verdict.bypass else []) + ([image.tag] if image else []) + (
                ["[reaction: allowed]"] if reaction else [])
            # What she remembers about the speaker and the topic. Reading memory changes nothing,
            # so it happens on a flagged turn too.
            bank = self._config.memory.banks.get(guild_id) if self._hindsight is not None else None
            remembered, found = "", []
            known_as = tuple(dict.fromkeys([last.author_name, *room_log.names_of(conn, last.author_id)]))
            if bank:
                # Memory is asked with the members' lines among the last few of the conversation in
                # front of the new message, so that a follow-up finds the facts of what it follows.
                # Her own lines and her name are left out of the question: with them in, memory
                # answered with facts about her whatever was asked (seen on 9 October 2026).
                her = getattr(getattr(trigger.guild, "me", None), "display_name", None) or "Sanya"
                earlier = [(None if entry.role == "assistant" else prompt.speaker_name(entry.author_name), entry.text)
                           for entry in entries[:len(entries) - min(len(said), len(entries))]]
                settings = self._config.memory
                query, lines = recall.asked(earlier, f"{last.author_name}: " + " ".join(texts),
                                            settings.recall_lines, settings.recall_chars, her)
                # At the same moment memory is asked what it holds about the person she is answering.
                found = await recall.remember(
                    self._hindsight, bank, query, settings.recall_facts, lines,
                    asker_names=known_as, asker_limit=settings.recall_asker_facts,
                    her_names=tuple(dict.fromkeys([her, "Sanya"])), own_limit=settings.recall_own_facts)
                remembered = recall.render(found)
            names = tuple({entry.author_name for entry in entries if entry.author_name} | set(around.names))
            # She is told who she is answering, and which of the people named in the message she is not.
            asker = prompt.speaker_name(last.author_name)
            own = {name.casefold() for name in known_as}
            spoken = " ".join(texts)
            others = [name for name in sorted(names) if name.casefold() not in own
                      and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", spoken, re.IGNORECASE)]
            others = others[:self._config.context.named_others]
            closing = f"[You are answering {asker}" + (f", not {' or '.join(others)}" if others else "") + ".]"
            messages = prompt.build(self._persona.text, entries,
                                    server=getattr(trigger.guild, "name", ""),
                                    channel=getattr(trigger.channel, "name", ""),
                                    now=datetime.fromtimestamp(started).astimezone(),
                                    pictures=[*around.pictures, *pictures],
                                    lead="\n".join(part for part in (remembered, around.text, *tags) if part),
                                    current=min(len(said), len(entries)),
                                    tail=closing)
            notes: list[str] = []
            context = ToolContext(guild_id, channel_id, names, last.author_id, known_as,
                                  last.author_id == self._config.discord.owner_user_id, bank, image, reaction,
                                  tuple(item.incoming.message_id for item in items), verdict.flagged)
            try:
                outcome = await loop.run(self._model, messages, None if verdict.bypass else self._toolbox, context,
                                         self._config.model.max_calls_per_reply,
                                         self._config.model.tools_per_call)
                text, failed, notes = outcome.text, False, outcome.notes
            except ModelFailure:
                text, failed = self._persona.line("failure"), True
            # On a turn that allowed it she may have answered with nothing but an emoji. If she also
            # attached an image, the image is still posted, without words.
            lone = (not failed and not text and reaction is not None and reaction.alone and reaction.chosen is not None
                    and (image is None or (image.chosen is None and image.sticker is None)))
            stopped, chunks, cut, plain = None, [], False, False
            # What came of the image and the reaction she was allowed, for the event log.
            image_outcome = ("not_allowed" if image is None else "searched_unattached" if image.candidates
                             else "searched_nothing" if image.searches else "allowed_unused")
            reaction_outcome = "not_allowed" if reaction is None else "allowed_unused"
            # She is about to answer: a new message from here on is a new turn.
            if lone:
                turn.seal()
            else:
                if not failed:
                    stopped = egress.screen(text, request="\n".join(texts), secrets=self._secrets,
                                            pressed=verdict.flagged, model_names=self._config.send.model_names)
                    if stopped is not None:
                        # The reason is logged; what she nearly said is not.
                        events.log_event("reply_replaced", channel_id=channel_id, reason=stopped)
                        text = self._persona.line(egress.LINE_FOR[stopped])
                # The media grant: only an image this turn's search found and checked is uploaded.
                chosen = image.chosen if image is not None and not failed and stopped is None else None
                # One of the server's own stickers, chosen with her tool: it goes in place of an image.
                sticker = image.sticker if image is not None and not failed and stopped is None and chosen is None else None
                if not failed:
                    # A link she wrote herself is not a granted image. It is taken out of the text.
                    text = _IMAGE_LINK.sub("", text).strip()
                    if not text and chosen is None and sticker is None:
                        text = self._persona.line("image_failed")
                turn.seal()
                plain = last.author_is_bot and self._exchanges.take(channel_id, last.author_id) == "plain"
                chunks, cut = split_reply(text, self._config.send.max_messages, self._persona.line("cutoff"))
                try:
                    uploaded = await send_reply(
                        trigger, chunks or [""], plain=plain,
                        image=(chosen.data, "image" + _ENDING[chosen.mime]) if chosen else None, sticker=sticker)
                except TriggerGone:
                    # The member took the message back: she says nothing and nothing is kept.
                    events.log_event("reply_dropped", guild_id=guild_id, channel_id=channel_id,
                                     reason="message_deleted")
                    return
                if chosen is not None:
                    spontaneous = image.mode == "optional"
                    image_outcome = "attached" if uploaded else "upload_failed"
                    if uploaded:
                        ledger.record_image(conn, channel_id, chosen.sha, chosen.url, spontaneous, self._clock())
                        notes.append(f"you posted a {chosen.kind}: {chosen.title}")
                    elif not spontaneous:
                        # An image they asked for would not upload: she says so. A spontaneous one fails silently.
                        await trigger.channel.send(self._persona.line("image_failed"))
                    events.log_event("image_posted" if uploaded else "image_failed", guild_id=guild_id,
                                     channel_id=channel_id, spontaneous=spontaneous, bytes=len(chosen.data))
                    # Further images, when they asked for several: each is its own message after the reply,
                    # so that one failed upload costs only that image.
                    for other in image.extra if uploaded else ():
                        went = await send_image(trigger.channel, (other.data, "image" + _ENDING[other.mime]))
                        if went:
                            ledger.record_image(conn, channel_id, other.sha, other.url, spontaneous, self._clock())
                            notes.append(f"you posted a {other.kind}: {other.title}")
                        events.log_event("image_posted" if went else "image_failed", guild_id=guild_id,
                                         channel_id=channel_id, spontaneous=spontaneous, bytes=len(other.data))
                if sticker is not None:
                    spontaneous = image.mode == "optional"
                    if uploaded:
                        # The ledger holds the sticker's id where an image has its fingerprint: the same
                        # repeat limit, and an unasked one counts for the channel's cooldown like an image.
                        ledger.record_image(conn, channel_id, STICKER_LEDGER.format(id=sticker.id), "", spontaneous,
                                            self._clock())
                        notes.append(f"you sent a sticker: {' '.join(str(sticker.name).split())[:80]}")
                        events.log_event("sticker_sent", guild_id=guild_id, channel_id=channel_id,
                                         sticker_id=str(sticker.id), alone=not chunks)
                    else:
                        # Discord refused the sticker: her words went alone. With no words, a sticker they
                        # asked for is owned up to, and an unasked one fails silently, as an image does.
                        if not chunks and not spontaneous:
                            await trigger.channel.send(self._persona.line("image_failed"))
                        events.log_event("sticker_failed", guild_id=guild_id, channel_id=channel_id,
                                         sticker_id=str(sticker.id), alone=not chunks)
            if reaction is not None and reaction.chosen is not None:
                try:
                    await trigger.add_reaction(reaction.chosen)
                    ledger.record_reaction(conn, channel_id, self._clock())
                    reaction_outcome = "added"
                    events.log_event("reaction_added", guild_id=guild_id, channel_id=channel_id, alone=lone)
                except Exception as exc:  # a reaction is a nicety; it must not cost the turn
                    reaction_outcome = "failed"
                    events.log_event("reaction_failed", channel_id=channel_id, error=type(exc).__name__)
            # An overheard picture is shown to her once. Having answered, she has seen these.
            self._files.mark_shown(guild_id, around.shown, self._clock())

        # Later turns see one line about each tool she used, not what it returned.
        if notes:
            said[-1] = replace(said[-1], notes="\n".join(notes))
        # The failure line is not part of the conversation; what was said to her is.
        if not failed and chunks:
            said.append(Entry(channel_id, "assistant", "\n".join(chunks), self._clock()))
        # The untrusted mark is for the turn that answers the message: it is not kept in the conversation.
        history.append_many(conn, [replace(entry, untrusted=False) for entry in said])
        # What she had from memory for this reply is kept beside it, as a copy, for as long as the
        # conversation is: the facts recalled before she answered, and those a memory tool handed her.
        # A fact she was made to forget during the turn is not kept.
        had = [(recalled.RECALL, fact) for fact in found] + list(context.seen)
        kept = [(source, fact.id, fact.text) for source, fact in had if fact.id not in context.forgotten]
        recalled.safely("recalled", lambda: recalled.save(conn, last.message_id, kept, self._clock()))
        self._on_activity()  # the quiet time before background work counts from her reply
        old = self._clock() - self._config.conversation.keep_days * DAY
        history.expire(conn, old)
        recalled.safely("recalled", lambda: recalled.expire(conn, old))
        events.log_event("reply_sent", guild_id=guild_id, channel_id=channel_id, sent=len(chunks),
                         chars=sum(len(chunk) for chunk in chunks), cut=cut, model_failed=failed,
                         plain=plain, window=len(entries), overheard=around.lines,
                         pictures=len(pictures) + len(around.pictures), tools=len(notes),
                         image_outcome=image_outcome, reaction_outcome=reaction_outcome,
                         tool_calls=context.tally["made"], tools_refused=context.tally["refused"],
                         ms=round((self._clock() - started) * 1000))

    async def close(self) -> None:
        await self._model.close()
        await self._security.close()
        if self._hindsight is not None:
            await self._hindsight.close()
