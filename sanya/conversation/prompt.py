"""Prompt assembly: the persona, where she is, and the conversation window.

The persona and rules come from files under persona/. Nothing here tells her
how to behave; this module only lays the pieces out for the model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from ..store.history import Entry

MAX_NAME = 32


def speaker_name(name: str | None) -> str:
    """A display name made safe to sit at the start of a line."""
    cleaned = " ".join((name or "").split())[:MAX_NAME].strip()
    return cleaned or "someone"


def captioned(caption: str, urls: Sequence[str]) -> list[tuple[str, str]]:
    """Pictures under one caption: the caption goes with the first, the rest follow it."""
    return [(caption if index == 0 else "", url) for index, url in enumerate(urls)]


def picture_parts(pictures: Sequence) -> list[dict]:
    """Pictures as the model is given them. Each may come with a caption saying what it is."""
    parts: list[dict] = []
    for one in pictures:
        caption, url = one if isinstance(one, tuple) else ("", one)
        if caption:
            parts.append({"type": "text", "text": caption})
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def clock(now: datetime) -> str:
    """The time as a person would say it: 1:41 in the afternoon."""
    hour = now.hour
    part = ("at night" if hour < 5 or hour >= 22 else "in the morning" if hour < 12
            else "in the afternoon" if hour < 18 else "in the evening")
    return f"{hour % 12 or 12}:{now.minute:02d} {part}"


def scene(server: str, channel: str, now: datetime) -> str:
    return (f"Server: {speaker_name(server)}. Channel: #{speaker_name(channel)}. "
            f"It is {now.strftime('%A')} {now.day} {now.strftime('%B %Y')}, {clock(now)}.")


def build(persona: str, entries: list[Entry], *, server: str, channel: str, now: datetime,
          pictures: Sequence = (), lead: str = "", current: int = 0, tail: str = "") -> list[dict]:
    """Chat messages for the model. ``entries`` is the window, oldest first.

    Members' messages carry the speaker's name. Messages in a row from members
    are merged into one, so the roles always alternate. ``pictures`` are the
    images shown with this turn, each a link or a (caption, link) pair; they go
    with the last message.
    ``lead`` is put in front of this turn's messages, which are the last
    ``current`` entries: it is where the room context goes. ``tail`` closes the
    turn: it is where she is told who she is answering.
    """
    messages = [{"role": "system", "content": persona.strip() + "\n\n" + scene(server, channel, now)}]
    first_of_turn = len(entries) - current
    for index, entry in enumerate(entries):
        if entry.role == "user":
            mark = " (untrusted)" if entry.untrusted else ""
            content = f"{speaker_name(entry.author_name)}{mark}: {entry.text}"
            for note in (entry.notes or "").splitlines():
                content += f"\n[{note}]"
            if lead and current and index == first_of_turn:
                content = f"{lead}\n{content}"
        else:
            content = entry.text
        if messages[-1]["role"] == entry.role:
            messages[-1]["content"] += "\n" + content
        else:
            messages.append({"role": entry.role, "content": content})
    if tail and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + tail
    if pictures and messages[-1]["role"] == "user":
        messages[-1]["content"] = [{"type": "text", "text": messages[-1]["content"]}] + picture_parts(pictures)
    return messages
