"""The media decision: is this turn off, optional or requested.

Requested: the member asked for an image. Optional: a spontaneous reaction
image is allowed this turn; code rolls the dice and checks the cooldown and
the member's wish, and she judges whether the mood suits it. Off: no image.
"""
from __future__ import annotations

import re
from typing import Optional

_MEDIA = r"(?:gifs?|images?|pictures?|pics?|photos?|memes?|stickers?)"
_VERBS = r"(?:post|send|show|share|give|find|get|drop|fetch|grab|pull up|look up)"
_ASK = re.compile(
    rf"(?i)\b{_VERBS}\b[^.!?\n]{{0,80}}\b{_MEDIA}\b|"  # 80 since 9 October 2026: at 40 a longer description was missed
    rf"\b{_MEDIA} of\b|"
    rf"^(?:(?:a|an|some|one|another)\s+)?(?:reaction\s+)?{_MEDIA}(?:\s+(?:please|pls|plz))?[!?.\s]*$")
# Talking about a picture that is already there is not asking for one.
_ABOUT_ONE = re.compile(rf"(?i)\b(?:what(?:'s| is| was)?|who(?:'s| is)?|describe|explain|see|look at)\b[^.!?\n]{{0,40}}\b"
                        rf"(?:this|that|the|my|his|her|their)\s+{_MEDIA}\b")
_NO_MORE = re.compile(
    rf"(?i)\b(?:no|without|skip|stop|avoid|never|don[’']?t|do not|quit)\b[^,.!?\n]{{0,45}}\b{_MEDIA}\b|"
    rf"\b(?:text|words)[ -]only\b")
_AGAIN = re.compile(
    rf"(?i)\b{_MEDIA}\b[^,.!?\n]{{0,25}}\b(?:are |is )?(?:fine|ok|okay|allowed|welcome)\b(?: again)?|"
    rf"\byou (?:can|may)\b[^,.!?\n]{{0,25}}\b{_MEDIA}\b[^,.!?\n]{{0,15}}\bagain\b")
_MENTION = re.compile(r"^\s*(?:@\S+\s+)+")
_MEME = re.compile(r"(?i)\bmemes?\b")
_GIF = re.compile(r"(?i)\bgifs?\b")
_STICKER = re.compile(r"(?i)\bstickers?\b")


def _plain(text: str) -> str:
    return _MENTION.sub("", text or "").replace("’", "'").strip()


def wish(text: str) -> Optional[bool]:
    """What a message says about unasked-for images: True = none for me, False = fine again, None = nothing."""
    text = _plain(text)
    if _AGAIN.search(text):
        return False
    if _NO_MORE.search(text):
        return True
    return None


def asked_for_image(text: str) -> bool:
    """Did the member ask her to find or post an image."""
    text = _plain(text)
    # "No GIFs for me" and "you can send me GIFs again" are wishes about images, not requests for one.
    if _NO_MORE.search(text) or _AGAIN.search(text) or _ABOUT_ONE.search(text):
        return False
    return bool(_ASK.search(text))


def asked_for_meme(text: str) -> bool:
    """Did the member ask for a meme by that name, and not for a GIF."""
    return asked_for_image(text) and bool(_MEME.search(text)) and not _GIF.search(text)


def asked_for_sticker(text: str) -> bool:
    """Did the member ask for a sticker by that name, and not for a GIF or a meme."""
    return asked_for_image(text) and bool(_STICKER.search(text)) and not _GIF.search(text) and not _MEME.search(text)


_COUNT = r"(?:\d{1,2}|one|two|three|four|five|a few|a couple|some)"
_MORE = re.compile(
    # "post the other 3", "send another one", "i need 3", "give me two more", "show me the rest"
    rf"(?i)\b(?:{_VERBS[3:-1]}|need|want)\b[^.!?\n]{{0,25}}\b(?:another|others?|more|rest|next|again|{_COUNT})\b|"
    rf"\b{_COUNT} more\b|"
    # or nothing but the asking: "another one", "one more", "again", "more please"
    r"^(?:(?:and|ok|okay|now|pls|please)\s+)?(?:another(?: one)?|one more|more|again|the rest|next(?: one)?)"
    r"(?:\s+(?:please|pls|plz))?[!?.\s]*$")
MORE_CHARS = 80  # a longer message is talking about something else


def asked_for_more(text: str) -> bool:
    """Is this a short follow-up asking for another of what she just posted: "post the other 3", "one more"?

    It names no image, so it only counts when she has just posted one; ``decide`` is told that.
    """
    text = _plain(text)
    return len(text) <= MORE_CHARS and not _NO_MORE.search(text) and bool(_MORE.search(text))


def decide(texts: list[str], *, roll: float, chance: float, cooling_down: bool, no_media: bool,
           just_posted: bool = False) -> str:
    """ "requested", "optional" or "off" for a turn made of ``texts``. ``roll`` is a number from 0 to 1.

    ``just_posted`` is whether she posted an image or a sticker in the last few exchanges here:
    then a short "another one" or "post the other 3" is a request too.
    """
    if any(asked_for_image(text) for text in texts) or (just_posted and any(asked_for_more(text) for text in texts)):
        return "requested"
    if no_media or cooling_down or roll >= chance:
        return "off"
    return "optional"
