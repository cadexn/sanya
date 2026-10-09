"""Batching and overlap: who gets answered, when, and in what order.

- Rapid messages from one member are answered together, once that member has
  been quiet for the wait.
- If the same member writes again while she is still working on the reply, the
  turn starts over with every message. Once she has begun sending, the turn is
  sealed and a new message waits for its own turn.
- A channel runs one turn at a time, in the order members first wrote.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .. import events


@dataclass
class _Batch:
    author_id: str
    items: list[Any]
    last: float  # when the newest message arrived
    restarts: int = 0
    sealed: bool = False
    restart: bool = False  # the running turn was cancelled to start over


@dataclass
class _Channel:
    waiting: list[_Batch] = field(default_factory=list)
    running: Optional[_Batch] = None
    turn: Optional[asyncio.Future] = None
    worker: Optional[asyncio.Task] = None


class Turn:
    """What a running turn may know and do about its own batch."""

    def __init__(self, batch: _Batch):
        self._batch = batch

    @property
    def restarts(self) -> int:
        return self._batch.restarts

    def seal(self) -> None:
        """From here on a new message cannot restart this turn. Call before sending."""
        self._batch.sealed = True


Handler = Callable[[str, list[Any], Turn], Awaitable[None]]


class Batcher:
    def __init__(self, wait_seconds: float, max_restarts: Optional[int], handler: Handler,
                 clock: Callable[[], float] = time.monotonic):
        self._wait = wait_seconds
        self._max_restarts = max_restarts  # None = no cap
        self._handler = handler
        self._clock = clock
        self._channels: dict[str, _Channel] = {}

    @property
    def busy(self) -> bool:
        """True while any reply is waiting or being worked on."""
        return bool(self._channels)

    def add(self, channel_id: str, author_id: str, item: Any) -> None:
        """Queue one addressed message. Must be called from the event loop."""
        channel = self._channels.setdefault(channel_id, _Channel())
        now = self._clock()
        running = channel.running
        if (running is not None and running.author_id == author_id and not running.sealed
                and (self._max_restarts is None or running.restarts < self._max_restarts)
                and channel.turn is not None and channel.turn.cancel()):
            running.items.append(item)
            running.last = now
            running.restarts += 1
            running.restart = True
        else:
            for batch in channel.waiting:
                if batch.author_id == author_id:
                    batch.items.append(item)
                    batch.last = now
                    break
            else:
                channel.waiting.append(_Batch(author_id, [item], now))
        if channel.worker is None or channel.worker.done():
            channel.worker = asyncio.ensure_future(self._work(channel_id, channel))

    def discard(self, channel_id: str, gone: Callable[[Any], bool]) -> int:
        """Drop queued messages for which ``gone`` is true. Returns how many were dropped.

        A turn still being worked on starts over without them, or is abandoned
        if nothing is left. A turn already sending is left to finish.
        """
        channel = self._channels.get(channel_id)
        if channel is None:
            return 0
        dropped = 0
        for batch in list(channel.waiting):
            kept = [item for item in batch.items if not gone(item)]
            dropped += len(batch.items) - len(kept)
            batch.items[:] = kept
            if not kept:
                channel.waiting.remove(batch)
        running = channel.running
        if running is not None and not running.sealed:
            kept = [item for item in running.items if not gone(item)]
            if len(kept) != len(running.items) and channel.turn is not None and channel.turn.cancel():
                dropped += len(running.items) - len(kept)
                running.items[:] = kept
                running.restart = bool(kept)
        return dropped

    async def _work(self, channel_id: str, channel: _Channel) -> None:
        while channel.waiting:
            batch = channel.waiting[0]
            remaining = batch.last + self._wait - self._clock()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            channel.waiting.pop(0)
            channel.running = batch
            channel.turn = asyncio.ensure_future(self._handler(channel_id, list(batch.items), Turn(batch)))
            await asyncio.wait({channel.turn})
            turn, channel.turn, channel.running = channel.turn, None, None
            if turn.cancelled():
                if batch.restart:
                    # Back to the front: the member keeps their place in the order.
                    batch.restart = False
                    channel.waiting.insert(0, batch)
                    events.log_event("turn_restarted", channel_id=channel_id, author_id=batch.author_id,
                                     messages=len(batch.items), restarts=batch.restarts)
            elif turn.exception() is not None:
                events.log_event("handler_error", where="turn", channel_id=channel_id,
                                 error=type(turn.exception()).__name__)
        # No await between the loop's last check and here, so nothing can be queued in the gap.
        if self._channels.get(channel_id) is channel:
            del self._channels[channel_id]

    async def close(self) -> None:
        """Stop every waiting and running turn."""
        tasks = []
        for channel in list(self._channels.values()):
            channel.waiting.clear()
            for task in (channel.worker, channel.turn):
                if task is not None and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.wait(tasks)
        self._channels.clear()
