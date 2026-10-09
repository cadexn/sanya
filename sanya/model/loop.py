"""The tool loop: the model may call tools between its calls, within a fixed budget.

The last call of the budget is made with tools switched off, so she always
has to answer with what she has.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..conversation.prompt import picture_parts
from .client import RETRY_NOTE, ModelFailure, with_note


@dataclass
class Outcome:
    text: str
    notes: list[str] = field(default_factory=list)  # one line per tool use, kept for later turns
    calls: int = 0  # model calls made


async def run(model: Any, messages: list[dict], toolbox: Optional[Any], context: Any, max_calls: int,
              tools_per_call: int) -> Outcome:
    """Call the model until it answers in words. ``toolbox`` None means a turn without tools.

    At most ``tools_per_call`` tool requests are honoured from one model answer.
    """
    notes: list[str] = []
    for number in range(1, max_calls + 1):
        offered = toolbox is not None and number < max_calls
        completion = await model.complete(messages, tools=toolbox.schemas_for(context) if offered else None)
        if not completion.tool_calls:
            return Outcome(completion.text, notes, number)
        if toolbox is None and number == 1 and max_calls > 1:
            # A turn without tools (a flagged one), and she asked for one all the same, as when the
            # message asks for a search. She is asked once more, for her words; the conversation is
            # left as it was. Before, this posted the failure line.
            completion = await model.complete(with_note(messages, RETRY_NOTE), tools=None)
            if not completion.tool_calls:
                return Outcome(completion.text, notes, 2)
        if not offered:
            # It asked for a tool it was not offered. There is nothing to post.
            raise ModelFailure("tool call without tools")
        messages.append({"role": "assistant", "content": completion.text or "",
                         "tool_calls": list(completion.tool_calls)})
        pictures: list = []
        done, said = False, ""
        for index, call in enumerate(completion.tool_calls):
            if index < tools_per_call:
                result = await toolbox.run(call, context)
                done = done or result.final
                said = said or result.reply
                content = result.text
                if result.note:
                    notes.append(result.note)
                pictures.extend(result.pictures)
            else:
                content = '{"success": false, "error": "too_many_tool_calls"}'
                context.tally["refused"] += 1
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})
        if done:
            # A tool call was itself her answer: a lone emoji reaction, with nothing to say, or an
            # image attached with the line that goes with it.
            return Outcome(said, notes, number)
        if pictures:
            messages.append({"role": "user", "content": [{"type": "text", "text": "[what you asked to see]"}]
                             + picture_parts(pictures)})
    raise ModelFailure("call budget spent")
