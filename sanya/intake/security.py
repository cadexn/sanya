"""The security check: a separate model call that screens each message addressed to her.

A flagged message is still answered, in character, but that turn gets no memory
tools and no memory writes, and the message is marked untrusted while it is
being answered. Whether a check that fails, times out or answers unclearly
counts as flagged is a setting; since 9 October 2026 it does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.client import ModelFailure


@dataclass(frozen=True)
class Verdict:
    flagged: bool
    reason: str  # "clean", "flagged", "bypass", "check_failed", "check_unclear" or "disabled"

    @property
    def bypass(self) -> bool:
        """The message tells her to ignore her rules, her limits or her character: the turn gets no tools at all."""
        return self.flagged and self.reason == "bypass"


class SecurityCheck:
    def __init__(self, enabled: bool, model: Any, instruction: str, flag_when_failed: bool = True):
        self._enabled = enabled
        self._flag_when_failed = flag_when_failed  # does a check that gave no verdict count as flagged
        self._model = model  # a ModelClient set up for the check: thinking off, short timeout, no retry
        self._instruction = instruction

    async def check(self, texts: list[str]) -> Verdict:
        """Screen the messages of one turn together."""
        if not self._enabled:
            return Verdict(False, "disabled")
        messages = [{"role": "system", "content": self._instruction},
                    {"role": "user", "content": "\n".join(texts)}]
        try:
            answer = (await self._model.complete(messages)).text.strip().upper()
        except ModelFailure:
            return Verdict(self._flag_when_failed, "check_failed")
        if answer.startswith("SAFE"):
            return Verdict(False, "clean")
        if answer.startswith("BYPASS"):
            return Verdict(True, "bypass")
        if answer.startswith("FLAG"):
            return Verdict(True, "flagged")
        return Verdict(self._flag_when_failed, "check_unclear")

    async def close(self) -> None:
        await self._model.close()
