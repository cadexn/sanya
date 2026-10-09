"""Ambient memory: the background work that turns overheard chat into lasting facts.

It runs only while nobody is talking to her. One piece of work is done at a
time, and after each she looks again whether someone has addressed her:

  1. describe one picture that has no description yet
  2. take back the facts of a deleted message
  3. send one batch of chat to the memory service
  4. retire unconfirmed hearsay that has had its 90 days
  5. ask the service to merge facts into summaries

A batch goes to the service as two documents. The first is what the members said, without her
lines: facts about people can then only come from people's own words. The second is her lines,
and from it only facts that involve nobody but her are kept. In the same way, what she is like,
did or promised is remembered only when she said it herself: a member's claim about her ("you
promised me the last biscuit") is not kept. Sent as one document, her answers
were read as chat like any other, so that whatever she recited from memory was stored again as a
new fact, and a fact a member had asked her to forget came back. The service was told not to keep
what she says about others and did so regardless; so the rule is enforced here, in code.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from .. import events
from ..config import Config
from ..intake.attachments import FileStore
from ..model.client import ModelFailure
from ..store import recalled
from ..store.db import Store
from .hindsight import Fact, Hindsight, MemoryUnavailable

TICK = 15  # seconds between looks for work
DAY = 86400
# What the memory service is told about a batch: how it is laid out and when it was said. The
# wording was tested with the bank's settings. Without the date rule, "moving in june", said in
# October 2026, was stored as June 2026.
LAYOUT = ("Group chat in the #{channel} channel of a Discord server. Each line is '[time] speaker: message'. "
          "The speakers are different people. Lines marked [bot] are bots. The chat took place on {date}. "
          "A month or a day named without a year is the next one to come after that date, unless the speaker "
          "is plainly talking about the past.")


def layout(channel: str, when: float) -> str:
    """The context line for a batch from ``channel`` whose first message was sent at ``when``."""
    return LAYOUT.format(channel=channel, date=_day(when))


# What the service is told about the document of her own lines.
OWN_LAYOUT = ("Things said by {name} in the #{channel} channel of a Discord server. Each 'Asked:' line is what someone "
              "said to her, given only so that her answer can be understood. The chat took place on {date}. "
              "A month or a day named without a year is the next one to come after that date, unless she is "
              "plainly talking about the past.")
OWN = "-own"  # the ending of the id of a batch's second document

Describe = Callable[[list[str]], Awaitable[str]]


def _day(when: float) -> str:
    day = datetime.fromtimestamp(when).astimezone()
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}"


def batch_text(rows: list[sqlite3.Row], descriptions: dict[str, list[str]]) -> str:
    """The chat lines of a batch, as the memory service reads them. Her own lines are not among them."""
    lines = []
    for row in rows:
        if row["own"]:
            continue
        who = row["author_name"] + (" [bot]" if row["is_bot"] else "")
        said = " ".join(row["text"].split())
        for note in descriptions.get(row["message_id"], ()):
            said = f"{said} {note}".strip()
        lines.append(f"[{datetime.fromtimestamp(row['created_at']):%H:%M}] {who}: {said}")
    return "\n".join(lines)


def own_text(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> str:
    """Her own lines of a batch, each after the message it answered, with no name on that message.

    A reply to a message that is no longer in the log is left out: the message was deleted, and
    her answer to it repeats what it said.
    """
    lines = []
    for row in rows:
        if not row["own"]:
            continue
        if row["reply_to"]:
            asked = conn.execute("SELECT text FROM room_log WHERE message_id = ?", (row["reply_to"],)).fetchone()
            if asked is None:
                continue
            said = " ".join(re.sub(rf"@{re.escape(row['author_name'])}\b", "", asked["text"]).split())
            if said:
                lines.append(f"Asked: {said}")
        lines.append(f"[{datetime.fromtimestamp(row['created_at']):%H:%M}] {row['author_name']}: {' '.join(row['text'].split())}")
    return "\n".join(lines)


_INVOLVING = re.compile(r"\|\s*Involving:\s*([^|]*)")
# How the service writes of a person it has no name for. A fact that speaks of one is not about her alone.
_UNNAMED = re.compile(r"(?i)\b(?:the\s+)?(?:speaker|asker|questioner|requester|user|someone|somebody|another\s+(?:person|member)|"
                      r"(?:a|the|other)\s+(?:member|person|people))(?:'s|\u2019s)?\b")
# A record that someone asked or told her to do or answer something. It is not a fact about anyone.
_ASKED_HER = (r"(?i)\b(?:(?:asked|asks|requested|requests|ordered|instructed|begged)\s+(?:that\s+)?{her}\b"
              r"|(?:told|tells|wanted|wants)\s+{her}\s+(?:not\s+)?to\b)")


def standing_wish(words: list[str]) -> re.Pattern:
    """...unless it has one of these words in it: then it is a wish about how she is to treat them, and is kept."""
    return re.compile(r"(?i)\b(?:" + "|".join(re.escape(word.strip()) for word in words) + r")\b")


def involved(text: str) -> list[str]:
    """The people the service names as involved in a fact, from the part it appends to the text."""
    found = _INVOLVING.search(text)
    return [name.strip().rstrip(".") for name in found.group(1).split(",") if name.strip()] if found else []


def only_about_her(fact: Fact, her_names: set[str], other_names: set[str]) -> bool:
    """Is this fact, drawn from her own lines, about her and nobody else?

    ``her_names`` and ``other_names`` are lower-cased: the names she has gone by, and those of
    everyone else seen in the server.
    """
    named = involved(fact.text) or list(fact.entities)
    if not any(name.casefold() in her_names for name in named):
        return False
    if _INVOLVING.search(fact.text) and any(name.casefold() not in her_names for name in involved(fact.text)):
        return False
    body = fact.text.split("|")[0].casefold()
    if _UNNAMED.search(body):
        return False
    return not any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", body) for name in other_names if name)


def about_her(fact: Fact, her_names: set[str]) -> bool:
    """Is this fact, drawn from what the members said, a claim about her: what she is like, did, said or promised?

    True when she is the subject ("Sanya promised Ben the last biscuit", "Ben and Sanya made
    a bet") or the one reported on ("Ben said that Sanya's favourite food is bacon"). Not when
    she is only the one spoken to ("Anna asked Sanya not to swear at her", "Ben told Sanya
    that he is moving").
    """
    body = fact.text.split("|")[0].casefold()

    def first(name: str) -> int:
        found = re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", body)
        return found.start() if found else -1

    spots = [spot for spot in (first(name) for name in her_names if name) if spot >= 0]
    if not spots:
        return False
    hers = min(spots)
    before = body[:hers]
    if re.search(r"\b(?:asked|asks|requested|requests|ordered|instructed|begged|demanded)\s+that\s+$", before):
        return False  # "... requested that Sanya not ...": something asked of her, not a claim about her
    if re.search(r"\bthat\s+$", before):
        return True  # "... said that Sanya ..."
    if re.search(r"\band\s+$", before) and len(before.split()) <= 4:
        return True  # "Ben and Sanya ..."
    others = [spot for spot in (first(name) for name in involved(fact.text) if name.casefold() not in her_names)
              if spot >= 0]
    return all(hers < spot for spot in others)  # she is named before anyone else: the fact is about her


def request_to_her(fact: Fact, her_names: set[str], standing: re.Pattern) -> bool:
    """Is this fact only a record that someone asked or told her to do or answer something?

    ``standing`` finds the words that make it a lasting wish instead; see ``standing_wish``.
    """
    body = fact.text.split("|")[0]
    asked = any(re.search(_ASKED_HER.format(her=re.escape(name)), body) for name in her_names if name)
    return asked and not standing.search(body)


class Ambient:
    def __init__(self, config: Config, store: Store, files: FileStore, hindsight: Hindsight, describe: Describe, *,
                 busy: Callable[[], bool], channel_name: Callable[[str], str],
                 clock: Callable[[], float] = time.time):
        self._config = config
        self._store = store
        self._files = files
        self._hindsight = hindsight
        self._describe = describe
        self._busy = busy  # is a reply being worked on or waiting
        self._channel_name = channel_name
        self._clock = clock
        self._addressed_at = clock()
        self._hold: dict[str, float] = {}  # kind of work -> not before this time, after a failure
        self._merged_at: dict[str, float] = {}
        self._hearsay_at: dict[str, float] = {}
        self._unmerged: set[str] = set()  # banks that have had new facts since their last merge
        self._standing = standing_wish(config.memory.standing_wish_words)

    def touch(self) -> None:
        """Someone has addressed her, or she has just answered: background work waits."""
        self._addressed_at = self._clock()

    def quiet(self) -> bool:
        return not self._busy() and self._clock() - self._addressed_at >= self._config.memory.quiet_minutes * 60

    def _held(self, kind: str) -> bool:
        return self._clock() < self._hold.get(kind, 0)

    def _guilds(self) -> list[tuple[str, str]]:
        active = self._config.discord.active_servers
        return [(guild_id, bank) for guild_id, bank in self._config.memory.banks.items()
                if active is None or guild_id in active]

    async def run(self) -> None:
        """Work through the background jobs for as long as the bot runs."""
        while True:
            await asyncio.sleep(TICK)
            try:
                while self.quiet() and await self.step():
                    pass
            except Exception as exc:  # the worker must outlive any one bad job
                events.log_event("handler_error", where="ambient", error=type(exc).__name__)

    async def step(self) -> bool:
        """Do one piece of work. Returns False when there is nothing to do right now."""
        for guild_id, bank in self._guilds():
            conn = self._store.guild(guild_id)
            if (await self._describe_one(guild_id, conn) or await self._take_back_one(guild_id, bank, conn)
                    or await self._send_batch(guild_id, bank, conn)):
                return True
        for bank in {bank for _, bank in self._guilds()}:
            if await self._expire_hearsay(bank) or await self._merge(bank):
                return True
        return False

    # --- 1. pictures -----------------------------------------------------------

    async def _describe_one(self, guild_id: str, conn: sqlite3.Connection) -> bool:
        if self._held("describe"):
            return False
        row = conn.execute(
            "SELECT * FROM files WHERE description IS NULL AND mime LIKE 'image/%' AND created_at >= ?"
            " ORDER BY id LIMIT 1", (self._clock() - self._config.files.describe_days * DAY,)).fetchone()
        if row is None:
            return False
        started = self._clock()
        try:
            stored = self._files.for_messages(guild_id, [row["message_id"]])[row["message_id"]][row["position"]]
            text = " ".join((await self._describe(self._files.pictures(stored))).split())
            text = text[:self._config.files.description_chars]
        except ModelFailure:
            self._hold["describe"] = self._clock() + self._config.memory.back_off_minutes * 60
            events.log_event("describe_failed", guild_id=guild_id, file_id=row["id"])
            return False
        except OSError:
            text = ""  # the owner has cleared the file away; there is nothing to describe
        conn.execute("UPDATE files SET description = ? WHERE id = ?", (text, row["id"]))
        events.log_event("file_described", guild_id=guild_id, file_id=row["id"], chars=len(text),
                         ms=round((self._clock() - started) * 1000))
        return True

    def _notes(self, conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[str, list[str]]:
        """For each message, a note per attachment: a picture's description, or another file's name."""
        import json
        notes: dict[str, list[str]] = {}
        for row in rows:
            names = json.loads(row["attachments"])
            if not names:
                continue
            described = {found["position"]: found["description"] for found in conn.execute(
                "SELECT position, description FROM files WHERE message_id = ?", (row["message_id"],))}
            for position, name in enumerate(names):
                if described.get(position):
                    notes.setdefault(row["message_id"], []).append(f"[image: {described[position]}]")
                else:
                    label = name.replace("[", " ").replace("]", " ")[:60]
                    notes.setdefault(row["message_id"], []).append(f"[file: {label}]")
        return notes

    # --- 2. deleted messages ---------------------------------------------------

    async def _take_back_one(self, guild_id: str, bank: str, conn: sqlite3.Connection) -> bool:
        """Remove the facts of a deleted message by sending its batch again without it."""
        if self._held("memory"):
            return False
        pending = conn.execute("SELECT document_id FROM memory_removals LIMIT 1").fetchone()
        if pending is None:
            return False
        document_id = pending["document_id"]
        batch = conn.execute("SELECT * FROM memory_batches WHERE document_id = ?", (document_id,)).fetchone()
        rows = conn.execute("SELECT * FROM room_log WHERE memory_doc = ? ORDER BY created_at, rowid",
                            (document_id,)).fetchall()
        old: list[str] = []  # the facts this batch had: they go, or are drawn afresh under new ids
        try:
            if batch is None:
                outcome = "batch_unknown"
            elif len(rows) < batch["messages"]:
                # Part of the batch has already left the 7-day log. Its facts are kept, by decision.
                outcome = "batch_too_old"
            elif not any(not row["is_bot"] or row["own"] for row in rows) or batch["curated"]:
                # Nothing left to draw facts from, or a fact of this batch was forgotten by request:
                # reading the text again would bring that fact back, so the whole batch goes.
                old = await self._fact_ids(bank, document_id)
                await self._hindsight.delete_document(bank, document_id)
                await self._hindsight.delete_document(bank, document_id + OWN)
                conn.execute("UPDATE room_log SET memory_doc = NULL WHERE memory_doc = ?", (document_id,))
                conn.execute("DELETE FROM memory_batches WHERE document_id = ?", (document_id,))
                outcome = "batch_deleted"
            else:
                old = await self._fact_ids(bank, document_id)
                await self._retain(bank, conn, document_id, batch["channel_id"], rows)
                conn.execute("UPDATE memory_batches SET messages = ? WHERE document_id = ?", (len(rows), document_id))
                self._unmerged.add(bank)
                outcome = "batch_resent"
        except MemoryUnavailable as exc:
            self._hold["memory"] = self._clock() + self._config.memory.back_off_minutes * 60
            events.log_event("memory_failed", guild_id=guild_id, job="take_back", error=exc.kind)
            return False
        conn.execute("DELETE FROM memory_removals WHERE document_id = ?", (document_id,))
        self._drop_copies(bank, old)
        events.log_event("memory_taken_back", guild_id=guild_id, outcome=outcome)
        return True

    async def _fact_ids(self, bank: str, document_id: str) -> list[str]:
        """The ids of the facts a batch has in memory, from both of its documents."""
        return [fact.id for one in (document_id, document_id + OWN) for fact in await self._drawn(bank, one)]

    def _drop_copies(self, bank: str, fact_ids: list[str]) -> None:
        """Facts that have left memory go from the copies kept beside her replies too: a copy never outlives its fact."""
        if fact_ids:
            for guild_id, its_bank in self._guilds():
                if its_bank == bank:
                    conn = self._store.guild(guild_id)
                    recalled.safely("recalled", lambda: recalled.forget_facts(conn, fact_ids))

    # --- 3. batches ------------------------------------------------------------

    async def _send_batch(self, guild_id: str, bank: str, conn: sqlite3.Connection) -> bool:
        if self._held("memory"):
            return False
        settings = self._config.memory
        now = self._clock()
        ready = conn.execute(
            "SELECT channel_id, COUNT(*) AS waiting, MAX(created_at) AS newest FROM room_log"
            " WHERE memory_state = 'pending' GROUP BY channel_id"
            " HAVING waiting >= ? OR newest <= ? ORDER BY MIN(created_at) LIMIT 1",
            (settings.batch_messages, now - settings.flush_minutes * 60)).fetchone()
        if ready is None:
            return False
        channel_id = ready["channel_id"]
        rows = conn.execute(
            "SELECT * FROM room_log WHERE memory_state = 'pending' AND channel_id = ?"
            " ORDER BY created_at, rowid LIMIT ?", (channel_id, settings.batch_messages)).fetchall()
        ids = [row["message_id"] for row in rows]
        marks = ", ".join("?" * len(ids))
        if not any(not row["is_bot"] or row["own"] for row in rows):
            # Only other bots spoke. They are context, never a source of facts, so there is nothing to keep.
            conn.execute(f"UPDATE room_log SET memory_state = 'skip' WHERE message_id IN ({marks})", ids)
            return True
        document_id = f"{guild_id}-{channel_id}-{ids[0]}"
        started = self._clock()
        try:
            dropped = await self._retain(bank, conn, document_id, channel_id, rows)
        except MemoryUnavailable as exc:
            self._hold["memory"] = self._clock() + self._config.memory.back_off_minutes * 60
            events.log_event("memory_failed", guild_id=guild_id, job="batch", error=exc.kind)
            return False
        conn.execute(f"UPDATE room_log SET memory_state = 'done', memory_doc = ? WHERE message_id IN ({marks})",
                     [document_id, *ids])
        conn.execute("INSERT OR REPLACE INTO memory_batches (document_id, channel_id, messages, created_at, curated)"
                     " VALUES (?, ?, ?, ?, 0)", (document_id, channel_id, len(ids), now))
        self._unmerged.add(bank)
        events.log_event("memory_batch", guild_id=guild_id, channel_id=channel_id, messages=len(ids), dropped=dropped,
                         ms=round((self._clock() - started) * 1000))
        return True

    async def _drawn(self, bank: str, document_id: str) -> list[Fact]:
        """The facts the service drew from one document, and from no other."""
        listed = await self._hindsight.facts(bank, document_id=document_id, limit=100)
        # Checked again here: a listing that ignored the document would have facts of other batches retired.
        return [fact for fact in listed if fact.document_id == document_id]

    async def _retain(self, bank: str, conn: sqlite3.Connection, document_id: str, channel_id: str,
                      rows: list[sqlite3.Row]) -> int:
        """Send a batch as its two documents and retire what may not be kept. Returns how many facts were dropped.

        Sending a document again replaces what was drawn from it before, so this is safe to repeat.
        """
        when = rows[0]["created_at"]
        stamp = datetime.fromtimestamp(when, timezone.utc).isoformat()
        tags = ["discord", f"channel:{channel_id}"]
        channel = self._channel_name(channel_id)
        # The names she has gone by here, and everyone else's, as the service will have written them.
        hers = {row[0].casefold() for row in conn.execute("SELECT DISTINCT author_name FROM room_log WHERE own = 1")}
        hers |= {row["author_name"].casefold() for row in rows if row["own"]} | {"sanya"}
        others = {row[0].casefold() for row in conn.execute("SELECT DISTINCT name FROM member_names")} - hers
        dropped = 0

        people = batch_text(rows, self._notes(conn, rows))
        if any(not row["own"] and not row["is_bot"] for row in rows):
            await self._hindsight.retain(bank, people, document_id=document_id, context=layout(channel, when),
                                         timestamp=stamp, tags=tags)
            drawn = await self._drawn(bank, document_id)
            gone = 0
            for fact in drawn:
                if request_to_her(fact, hers, self._standing):
                    await self._hindsight.retire(bank, fact.id, "a request put to her, not a fact")
                    gone += 1
                elif about_her(fact, hers):
                    # What she is like, did or promised is remembered only from her own words.
                    await self._hindsight.retire(bank, fact.id, "said about her by someone else")
                    gone += 1
            dropped += gone
            if gone == len(drawn):
                await self._hindsight.delete_document(bank, document_id)  # nothing of it was kept, or nothing was drawn
        else:
            await self._hindsight.delete_document(bank, document_id)  # on a resend: no person's line is left

        own = own_text(conn, rows)
        if own:
            name = next(row["author_name"] for row in rows if row["own"])
            await self._hindsight.retain(bank, own, document_id=document_id + OWN, timestamp=stamp, tags=tags,
                                         context=OWN_LAYOUT.format(name=name, channel=channel, date=_day(when)))
            kept = 0
            for fact in await self._drawn(bank, document_id + OWN):
                if only_about_her(fact, hers, others):
                    kept += 1
                else:
                    await self._hindsight.retire(bank, fact.id, "said by her about someone else")
                    dropped += 1
            if not kept:
                await self._hindsight.delete_document(bank, document_id + OWN)
        else:
            await self._hindsight.delete_document(bank, document_id + OWN)
        return dropped

    # --- 4. hearsay ------------------------------------------------------------

    async def _expire_hearsay(self, bank: str) -> bool:
        """Once a day, retire unconfirmed hearsay older than the limit."""
        now = self._clock()
        if self._held("memory") or now - self._hearsay_at.get(bank, 0) < DAY:
            return False
        self._hearsay_at[bank] = now
        limit = datetime.fromtimestamp(now - self._config.memory.hearsay_keep_days * DAY, timezone.utc)
        retired: list[str] = []
        offset = 0
        try:
            while True:
                page = await self._hindsight.facts(bank, q="(unconfirmed)", limit=100, offset=offset)
                for fact in page:
                    if "(unconfirmed)" in fact.text and _before(fact.mentioned_at, limit):
                        await self._hindsight.retire(bank, fact.id, "unconfirmed hearsay past its keeping time")
                        retired.append(fact.id)
                if len(page) < 100:
                    break
                offset += 100
        except MemoryUnavailable as exc:
            self._drop_copies(bank, retired)  # those retired before the service went quiet
            events.log_event("memory_failed", job="hearsay", error=exc.kind)
            return False
        self._drop_copies(bank, retired)
        events.log_event("hearsay_expired", facts=len(retired))
        return len(retired) > 0

    # --- 5. merging ------------------------------------------------------------

    async def _merge(self, bank: str) -> bool:
        settings = self._config.memory
        now = self._clock()
        if (bank not in self._unmerged or self._held("memory")
                or now - self._addressed_at < settings.merge_idle_minutes * 60
                or now - self._merged_at.get(bank, 0) < settings.merge_every_minutes * 60):
            return False
        self._merged_at[bank] = now
        self._unmerged.discard(bank)
        try:
            await self._hindsight.merge(bank)
        except MemoryUnavailable as exc:
            events.log_event("memory_failed", job="merge", error=exc.kind)
            return False
        events.log_event("memory_merge_asked")
        return False  # the service does the merging itself; there is nothing more to do here


def _before(moment: Optional[str], limit: datetime) -> bool:
    if not moment:
        return False
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < limit


def forget_messages(conn: sqlite3.Connection, message_ids: list[str]) -> int:
    """Before deleted messages leave the room log, note which batches must be taken back. Returns how many."""
    marks = ", ".join("?" * len(message_ids))
    batches = conn.execute(
        f"SELECT memory_doc, COUNT(*) FROM room_log WHERE memory_doc IS NOT NULL AND message_id IN ({marks})"
        " GROUP BY memory_doc", message_ids).fetchall()
    for document_id, deleted in batches:
        conn.execute("INSERT OR IGNORE INTO memory_removals (document_id) VALUES (?)", (document_id,))
        # Each batch remembers how many of its messages should still be in the log.
        conn.execute("UPDATE memory_batches SET messages = messages - ? WHERE document_id = ?", (deleted, document_id))
    return len(batches)
