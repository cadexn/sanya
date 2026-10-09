"""Sending: a reply to the triggering message, without a ping, in at most a few messages."""
from __future__ import annotations

import io
from typing import Any, Optional

import discord

DISCORD_LIMIT = 2000  # characters in one Discord message


class TriggerGone(Exception):
    """The message she was answering was deleted before her reply went out."""


def _gone(exc: discord.HTTPException) -> bool:
    # Discord refuses a reply to a deleted message as an invalid message reference.
    return exc.code == 10008 or (exc.code == 50035 and "message_reference" in (exc.text or ""))


def _break(text: str, limit: int) -> int:
    """Where to cut ``text`` so the first part fits: a paragraph, a line, a word, or flat."""
    for separator in ("\n\n", "\n", " "):
        at = text.rfind(separator, 0, limit + 1)
        if at > limit // 2:
            return at
    return limit


def split_reply(text: str, max_messages: int, cutoff_line: str, limit: int = DISCORD_LIMIT) -> tuple[list[str], bool]:
    """Split a reply into Discord messages. Returns the messages and whether it was cut.

    A reply too long for ``max_messages`` is cut, and the last message ends
    with ``cutoff_line``.
    """
    rest = text.strip()
    chunks: list[str] = []
    while rest and len(chunks) < max_messages:
        if len(rest) <= limit:
            chunks.append(rest)
            rest = ""
        else:
            at = _break(rest, limit)
            chunks.append(rest[:at].rstrip())
            rest = rest[at:].lstrip()
    if not rest:
        return chunks, False
    room = limit - len(cutoff_line) - 1
    last = chunks[-1]
    if len(last) > room:
        last = last[:_break(last, room)].rstrip()
    chunks[-1] = last + "\n" + cutoff_line
    return chunks, True


async def send_image(channel: Any, image: tuple[bytes, str]) -> bool:
    """Post one more image as a message of its own, after her reply. Tried once; returns whether it went up."""
    try:
        await channel.send(None, file=discord.File(io.BytesIO(image[0]), filename=image[1]))
    except discord.HTTPException:
        return False
    return True


async def send_reply(trigger: Any, chunks: list[str], plain: bool = False,
                     image: Optional[tuple[bytes, str]] = None, ping: Optional[int] = None,
                     sticker: Any = None) -> bool:
    """Post ``chunks``: the first as a reply to ``trigger``, the rest as plain messages.

    With ``plain`` nothing is a reply: every chunk is an ordinary message in the channel.
    ``image`` is the bytes and file name of one image to upload with the first message. The
    upload is tried twice; if it still fails the text goes without it. Returns whether the
    image went up (True when there was none to send).
    ``sticker`` is one of the server's own stickers to send with the first message, in place of
    an image. It is tried once; if Discord refuses it her words go alone, and the return is False.
    ``ping`` is the one account her message may notify, if she mentioned it; nobody else ever is.
    Raises TriggerGone, with nothing posted, if the message to reply to has been deleted.
    """
    first, rest = chunks[0], chunks[1:]
    allowed = {} if ping is None else {
        "allowed_mentions": discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                                    users=[discord.Object(id=ping)])}

    async def post(with_extra: bool) -> None:
        extra = dict(allowed)
        if with_extra and image is not None:
            extra["file"] = discord.File(io.BytesIO(image[0]), filename=image[1])
        elif with_extra and sticker is not None:
            extra["stickers"] = [sticker]
        if plain:
            await trigger.channel.send(first or None, **extra)
        else:
            await trigger.reply(first or None, mention_author=False, **extra)

    nothing_extra = image is None and sticker is None
    uploaded = nothing_extra
    try:
        for _ in range(2 if image is not None else 0 if sticker is None else 1):
            try:
                await post(True)
                uploaded = True
                break
            except discord.HTTPException as exc:
                if _gone(exc):
                    raise
        if nothing_extra or (not uploaded and first):
            await post(False)
    except discord.HTTPException as exc:
        if _gone(exc):
            raise TriggerGone() from None
        raise
    for chunk in rest:
        await trigger.channel.send(chunk, **allowed)
    return uploaded
