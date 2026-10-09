"""The model client: one chat completion, retried, or a clear failure.

The model server is shared and answers one request at a time, so a call can
spend a long while queued. Only timings and token counts are logged.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from .. import events
from ..config import Model


THINK_END = "</think>"  # where thinking stops, when the server lets it through in the text
# A line that is nothing but a note in square brackets. She is shown such notes; they are never her words.
_NOTE_LINE = re.compile(r"(?m)^[ \t]*\[[A-Za-z][^\]\n]*\][ \t]*$")
# Put at the end of the conversation on a retry. The same prompt can draw the same empty answer
# again whatever the seed, so the second try is not the same prompt. It says nothing of what
# happened: "no answer came through" was taken by her for the result of a page she had not read.
RETRY_NOTE = "[Write your message to them now: words only, no notes in brackets.]"


def spoken(text: str) -> str:
    """What of ``text`` is hers to post, for words that reach us in a tool call and not as an answer.

    Thinking that came through is dropped. A line that is only a note in brackets is not her words: "".
    """
    text = (text or "").rpartition(THINK_END)[2].strip()
    return "" if _NOTE_LINE.search(text) else text


def with_note(messages: list[dict], note: str) -> list[dict]:
    """The conversation with a note at its end, the roles left in order. ``messages`` is not changed."""
    last = messages[-1]
    if last.get("role") != "user":
        return messages + [{"role": "user", "content": note}]
    content = last["content"]
    if isinstance(content, list):
        content = content + [{"type": "text", "text": note}]
    else:
        content = f"{content}\n{note}"
    return messages[:-1] + [dict(last, content=content)]


class ModelFailure(Exception):
    """Every attempt failed. The caller posts the failure line."""


class _BadAnswer(Exception):
    """The server answered, but not with a usable reply."""


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    tool_calls: tuple = ()  # what the model asked to run, in the API's own form
    reasoning_tokens: int = 0  # thinking, counted within completion_tokens
    cached_tokens: int = 0  # of the prompt, already held by the server
    prompt_ms: int = 0  # the server's time reading the prompt
    predicted_ms: int = 0  # the server's time writing the answer


# Sends one request body, returns (HTTP status, parsed JSON).
Post = Callable[[dict], Awaitable[tuple[int, Any]]]


class ModelClient:
    def __init__(self, settings: Model, post: Optional[Post] = None, purpose: str = "reply",
                 temperature: Optional[float] = None):
        self._settings = settings
        self._temperature = temperature  # None leaves the server's own setting in place
        self._purpose = purpose  # named in the event log, so calls can be told apart
        self._post = post or self._http_post
        self._session: Optional[aiohttp.ClientSession] = None

    async def _http_post(self, body: dict) -> tuple[int, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._settings.timeout_seconds))
        url = self._settings.base_url.rstrip("/") + "/chat/completions"
        async with self._session.post(url, json=body) as response:
            return response.status, await response.json(content_type=None)

    def _body(self, messages: list[dict], tools: Optional[list[dict]] = None) -> dict:
        body: dict[str, Any] = {"model": self._settings.name, "messages": messages,
                                "max_tokens": self._settings.max_reply_tokens}
        if tools:
            body["tools"] = tools
        if self._temperature is not None:
            body["temperature"] = self._temperature
        # The server thinks unless told not to, and ignores a thinking budget:
        # max_tokens, which covers thinking and reply together, is the only limit.
        if not self._settings.thinking:
            body["reasoning_effort"] = "none"
        return body

    async def _once(self, body: dict) -> Completion:
        status, data = await self._post(body)
        if status != 200:
            raise _BadAnswer(f"http_{status}")
        try:
            choice = data["choices"][0]
            # The model's reasoning arrives in a separate field and is dropped here. Now and then
            # it comes through in the text instead: everything up to the closing mark goes too.
            text = (choice["message"].get("content") or "").rpartition(THINK_END)[2].strip()
            tool_calls = tuple(choice["message"].get("tool_calls") or ())
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, AttributeError):
            raise _BadAnswer("malformed") from None
        if not text and not tool_calls:
            raise _BadAnswer("empty_" + str(choice.get("finish_reason") or "answer"))
        if not tool_calls and _NOTE_LINE.search(text):
            raise _BadAnswer("stage_note")  # she wrote a note of the kind she is shown; it is not posted
        timings = data.get("timings") or {}
        return Completion(text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                          tool_calls,
                          int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                          int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                          round(timings.get("prompt_ms") or 0), round(timings.get("predicted_ms") or 0))

    async def complete(self, messages: list[dict], tools: Optional[list[dict]] = None) -> Completion:
        """Return the model's reply, or raise ModelFailure once the retries are spent.

        With ``tools`` the reply may be a request to run some of them instead of text.
        """
        body = self._body(messages, tools)
        attempts = self._settings.retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            if attempt > 1:
                body = dict(body, seed=random.randrange(1, 2 ** 31), messages=with_note(messages, RETRY_NOTE))
            try:
                completion = await self._once(body)
            except (_BadAnswer, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                error = str(exc) if isinstance(exc, _BadAnswer) else type(exc).__name__
                events.log_event("model_call_failed", purpose=self._purpose, attempt=attempt, error=error,
                                 ms=round((time.monotonic() - started) * 1000))
                continue
            events.log_event("model_call", purpose=self._purpose, attempt=attempt, ms=round((time.monotonic() - started) * 1000),
                             prompt_tokens=completion.prompt_tokens,
                             completion_tokens=completion.completion_tokens,
                             reasoning_tokens=completion.reasoning_tokens, cached_tokens=completion.cached_tokens,
                             prompt_ms=completion.prompt_ms, predicted_ms=completion.predicted_ms,
                             tool_calls=len(completion.tool_calls), offered=len(tools or ()))
            return completion
        raise ModelFailure(f"no usable answer in {attempts} attempts")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
