"""Recall: what she remembers that bears on a turn, and whose a fact is.

The memory service knows people by name, not by Discord account. A fact is
"about" the people it names, except the person who merely reported it.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Iterable, Optional

from .. import events
from .hindsight import Fact, Hindsight, MemoryUnavailable

HEADING = "[What you remember that may bear on this]"
_REPORTED = re.compile(r"^(.{1,60}?) said that ", re.IGNORECASE)
_INVOLVING = re.compile(r"\|\s*Involving:\s*([^|]*)")  # the people the service names at the end of a fact


def about(fact: Fact) -> set[str]:
    """The names a fact is about, lower-cased. Whoever only said it about someone else is left out."""
    names = {name.casefold() for name in fact.entities}
    reported = _REPORTED.match(fact.text)
    if reported and "(unconfirmed)" in fact.text:
        names.discard(reported.group(1).strip().casefold())
    return names


def is_about(fact: Fact, names: Iterable[str]) -> bool:
    """True if the fact is about someone who has gone by one of ``names``."""
    return bool(about(fact) & {name.casefold() for name in names if name})


def render(facts: list[Fact]) -> str:
    """The block she is shown before a turn, or nothing if she remembers nothing relevant."""
    if not facts:
        return ""
    return "\n".join([HEADING] + [f"- {' '.join(fact.text.split())}" for fact in facts])


def asked(earlier: list[tuple[Optional[str], str]], new: str, lines: int, chars: int, her: str = "") -> tuple[str, int]:
    """What memory is asked before a turn, and how many earlier lines are in it.

    ``earlier`` is the channel's conversation so far, oldest first, as (name, words), with no name
    on her own lines; ``new`` is the message being answered. Of the last ``lines`` of the
    conversation, those a member said go in front of it, one to a line, so that a follow-up ("and
    her?") is looked up with what it follows. Her own lines are left out, and so is a mention of
    her (``her`` is her name): with her name all through the question, memory answered with facts
    about her whatever was asked. The whole is cut to ``chars`` from the front, so the newest words
    always stay.
    """
    mention = re.compile(rf"@{re.escape(her)}(?!\w)", re.IGNORECASE) if her else None

    def plain(words: str) -> str:
        return " ".join((mention.sub(" ", words) if mention else words).split())

    recent = [f"{name}: {plain(words)}" for name, words in (earlier[-lines:] if lines > 0 else []) if name]
    whole = "\n".join([*recent, plain(new)])[-chars:].lstrip()
    return whole, whole.count("\n")


def _hers_alone(fact: Fact, hers: set[str]) -> bool:
    """Is this a fact about her and nobody else? ``hers`` is her names, lower-cased.

    The service ends a fact with the people it involves; that list is gone by where there is one,
    since the names it attaches to a fact include things as well as people.
    """
    found = _INVOLVING.search(fact.text)
    people = ({name.strip().rstrip(".").casefold() for name in found.group(1).split(",") if name.strip()}
              if found else about(fact))
    return bool(people) and people <= hers


def involves(fact: Fact, names: Iterable[str]) -> bool:
    """Is this recalled fact about someone who has gone by one of ``names``?

    A fact that comes back from a recall carries no names attached (seen against the service on
    9 October 2026: the field is empty), so ``about`` cannot tell. The people the service lists at
    the end of the fact's text decide; failing that, a name standing as a word in the text.
    Whoever only reported an unconfirmed thing about someone else is still left out.
    """
    wanted = {name.casefold() for name in names if name}
    found = _INVOLVING.search(fact.text)
    if found:
        people = {name.strip().rstrip(".").casefold() for name in found.group(1).split(",") if name.strip()}
    elif fact.entities:
        people = {name.casefold() for name in fact.entities}
    else:
        body = fact.text.casefold()
        people = {name for name in wanted if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", body)}
    reported = _REPORTED.match(fact.text)
    if reported and "(unconfirmed)" in fact.text:
        people.discard(reported.group(1).strip().casefold())
    return bool(people & wanted)


def chosen(topic: list[Fact], asker: list[Fact], asker_names: Iterable[str], her_names: Iterable[str], *,
           limit: int, asker_limit: int, own_limit: int) -> tuple[list[Fact], int]:
    """The facts put before her, and how many of them are the asker's own.

    First up to ``asker_limit`` facts about the person she is answering, then up to ``limit`` of
    the facts found for what was said. Of those, at most ``own_limit`` may be about nobody but
    herself: they keep her consistent, but they must not crowd out everything else.
    """
    asker_names, hers = list(asker_names), {name.casefold() for name in her_names if name}
    theirs = [fact for fact in asker if involves(fact, asker_names)][:max(asker_limit, 0)]
    seen = {fact.id for fact in theirs}
    rest: list[Fact] = []
    own = 0
    for fact in topic:
        if len(rest) >= limit:
            break
        if fact.id in seen:
            continue
        if _hers_alone(fact, hers):
            if own >= own_limit:
                continue
            own += 1
        seen.add(fact.id)
        rest.append(fact)
    return theirs + rest, len(theirs)


CANDIDATES = 4  # times the wanted number of facts asked of the service, so that some can be passed over


async def remember(hindsight: Hindsight, bank: str, query: str, limit: int, lines: int = 0, *,
                   asker_names: tuple[str, ...] = (), asker_limit: int = 0, her_names: tuple[str, ...] = (),
                   own_limit: Optional[int] = None) -> list[Fact]:
    """The facts for a turn. A memory service that is down costs her the facts, not the reply.

    ``lines`` is how many earlier lines of the conversation ``query`` carries, for the event log.
    With ``asker_names`` and ``asker_limit``, memory is asked a second time, at the same moment,
    for what it holds about the person she is answering; if that second question fails she has
    the first one's facts alone. ``own_limit`` caps the facts about nobody but herself.
    """
    if limit <= 0 or not query.strip():
        return []
    started = time.monotonic()
    capped = own_limit is not None and bool(her_names)
    wanted = [hindsight.recall(bank, query, limit * CANDIDATES if capped else limit)]
    if asker_limit > 0 and asker_names:
        wanted.append(hindsight.recall(bank, asker_names[0], asker_limit * CANDIDATES))
    topic, *more = await asyncio.gather(*wanted, return_exceptions=True)
    took = round((time.monotonic() - started) * 1000)
    for answer in (topic, *more):
        if isinstance(answer, BaseException) and not isinstance(answer, MemoryUnavailable):
            raise answer
    if isinstance(topic, MemoryUnavailable):
        events.log_event("recall_failed", error=topic.kind, ms=took)
        return []
    asker = more[0] if more and not isinstance(more[0], MemoryUnavailable) else []
    facts, theirs = chosen(topic, asker, asker_names, her_names, limit=limit, asker_limit=asker_limit,
                           own_limit=own_limit if capped else limit)
    extra = {"asker": theirs, "asker_failed": True} if more and isinstance(more[0], MemoryUnavailable) else \
        {"asker": theirs} if more else {}
    events.log_event("recall", facts=len(facts), lines=lines, ms=took, **extra)
    return facts
