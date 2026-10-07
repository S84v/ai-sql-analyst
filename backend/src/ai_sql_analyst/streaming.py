"""Pure translation of LangGraph stream events into the app event protocol (ADR-006).

This module is deliberately free of HTTP and SSE concerns: it does not import
FastAPI and only knows the event dicts produced by
``CompiledStateGraph.astream(..., stream_mode=["messages", "updates"], version="v2")``.
It yields plain dicts:

    {"type": "status", "message": str}
    {"type": "answer_delta", "text": str}
    {"type": "done"}
    {"type": "error", "message": str}

Privacy boundary: answer text is taken exclusively from ``AIMessageChunk.text``.
Raw model reasoning (DeepSeek ``reasoning_text`` items, reasoning content blocks,
``additional_kwargs``) is never read or emitted. Progress messages are derived
only from observable tool activity (tool names and the structured ``run_sql``
result), never from model chain-of-thought.

Answer text is buffered per agent turn and only emitted once the turn is known to
have no tool calls. A model can narrate before calling a tool in the same turn,
and that narration must not be mistaken for (or corrupt) the final answer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

# Concise, user-facing progress derived from an agent's tool decisions.
_TOOL_STATUS = {
    "get_schema": "Inspecting database schema",
    "run_sql": "Running analytical query",
}

_START_STATUS = "Analyzing question"
_PREPARING_STATUS = "Preparing final answer"
_REFINING_STATUS = "Refining query after an execution error"
_CHECKING_STATUS = "Checking query result"
_NO_ANSWER_MESSAGE = "The analysis did not produce an answer."


def _last_message(output: Any) -> Any | None:
    if not isinstance(output, Mapping):
        return None
    messages = output.get("messages")
    if isinstance(messages, list) and messages:
        return messages[-1]
    return None


def _tool_result_status(message: Any) -> str | None:
    """Map a ToolMessage to a status, or ``None`` when there is nothing to say.

    Only ``run_sql`` has a meaningful success/error progress distinction; the
    schema result is already covered by the request status. Malformed content is
    ignored rather than surfaced.
    """
    if getattr(message, "name", None) != "run_sql":
        return None
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    return _CHECKING_STATUS if payload.get("ok") else _REFINING_STATUS


async def translate_agent_events(
    events: AsyncIterator[Mapping[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Translate LangGraph v2 stream events into application events.

    Status messages are emitted in execution order and consecutive duplicates are
    collapsed, so repeated identical tool activity does not spam the client.
    """
    saw_answer = False
    preparing_emitted = False
    # Text for the current agent turn is buffered, not streamed immediately: a
    # model can narrate ("I'll inspect the schema...") and *then* call a tool in
    # the same turn, and that narration is not the final answer. The turn's text
    # is only emitted as answer deltas once the turn is known to have no tool
    # calls. Once a tool call is seen, the buffered text is discarded.
    turn_text: list[str] = []
    last_status: str | None = None

    def status(message: str) -> dict[str, Any] | None:
        nonlocal last_status
        if message == last_status:
            return None
        last_status = message
        return {"type": "status", "message": message}

    initial = status(_START_STATUS)
    if initial is not None:
        yield initial

    async for raw in events:
        kind = raw.get("type")
        data = raw.get("data")

        if kind == "messages":
            chunk, metadata = data
            if not isinstance(metadata, Mapping) or metadata.get("langgraph_node") != "agent":
                continue
            text = getattr(chunk, "text", "") or ""
            if getattr(chunk, "tool_call_chunks", None):
                # This turn is a tool turn; none of its text is the answer.
                turn_text = []
                continue
            if text:
                turn_text.append(text)
            continue

        if kind != "updates" or not isinstance(data, Mapping):
            continue

        for node, output in data.items():
            message = _last_message(output)
            if node == "agent":
                tool_calls = list(getattr(message, "tool_calls", None) or ())
                if not tool_calls and turn_text:
                    if not preparing_emitted:
                        preparing_emitted = True
                        preparing = status(_PREPARING_STATUS)
                        if preparing is not None:
                            yield preparing
                    for piece in turn_text:
                        saw_answer = True
                        yield {"type": "answer_delta", "text": piece}
                turn_text = []
                for tool_call in tool_calls:
                    message_text = _TOOL_STATUS.get(tool_call.get("name"))
                    if message_text is not None:
                        event = status(message_text)
                        if event is not None:
                            yield event
            elif node == "tools":
                if isinstance(output, Mapping):
                    for tool_message in output.get("messages", []) or ():
                        message_text = _tool_result_status(tool_message)
                        if message_text is not None:
                            event = status(message_text)
                            if event is not None:
                                yield event
            elif node == "timeout_stop":
                # The timeout budget was exhausted, so the graph emitted a
                # deterministic terminal AIMessage without another model turn.
                # Surface it as the answer (ADR-009) so the client receives the
                # grounded explanation instead of the generic "no answer" error.
                # It is not a streamed chunk, so it arrives only via the update.
                message = _last_message(output)
                text = getattr(message, "text", "") or ""
                if text:
                    saw_answer = True
                    yield {"type": "answer_delta", "text": text}

    if saw_answer:
        yield {"type": "done"}
    else:
        yield {"type": "error", "message": _NO_ANSWER_MESSAGE}
