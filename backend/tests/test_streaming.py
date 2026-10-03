"""Deterministic tests for the pure LangGraph-event -> app-event translation (ADR-006).

No HTTP, no model, no database. LangGraph v2 stream events are constructed
directly so the translator can be verified in isolation. Reasoning privacy is
asserted explicitly.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from ai_sql_analyst.streaming import translate_agent_events


def _agent_update(*messages: Any) -> dict:
    return {"type": "updates", "data": {"agent": {"messages": list(messages)}}}


def _tools_update(*messages: Any) -> dict:
    return {"type": "updates", "data": {"tools": {"messages": list(messages)}}}


def _chunk(chunk: AIMessageChunk) -> dict:
    return {"type": "messages", "data": (chunk, {"langgraph_node": "agent"})}


def _text_chunk(text: str) -> dict:
    return _chunk(AIMessageChunk(content=text))


def _tool_call(name: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": call_id, "type": "tool_call"}],
    )


def _tool_result(name: str, payload: dict, call_id: str = "c1") -> ToolMessage:
    return ToolMessage(content=json.dumps(payload), tool_call_id=call_id, name=name)


async def _aiter(items: Iterable[dict]):
    for item in items:
        yield item


def _collect(events: list[dict]) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in translate_agent_events(_aiter(events))]

    return asyncio.run(run())


def _statuses(events: list[dict]) -> list[str]:
    return [e["message"] for e in events if e["type"] == "status"]


def _answer(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "answer_delta")


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# Status derivation, ordering, and deduplication
# ---------------------------------------------------------------------------


def test_statuses_follow_tool_activity_in_order():
    events = _collect(
        [
            _agent_update(_tool_call("get_schema", "g1")),
            _tools_update(_tool_result("get_schema", {"tables": []}, "g1")),
            _agent_update(_tool_call("run_sql", "r1")),
            _tools_update(_tool_result("run_sql", {"ok": True}, "r1")),
            _text_chunk("There were "),
            _text_chunk("95 orders."),
            _agent_update(AIMessage(content="There were 95 orders.")),
        ]
    )

    assert _statuses(events) == [
        "Analyzing question",
        "Inspecting database schema",
        "Running analytical query",
        "Checking query result",
        "Preparing final answer",
    ]
    assert _answer(events) == "There were 95 orders."
    assert _types(events)[-1] == "done"


def test_failed_sql_emits_refining_status_and_loop_continues():
    events = _collect(
        [
            _agent_update(_tool_call("run_sql", "r1")),
            _tools_update(_tool_result("run_sql", {"ok": False, "error": {}}, "r1")),
            _agent_update(_tool_call("run_sql", "r2")),
            _tools_update(_tool_result("run_sql", {"ok": True}, "r2")),
            _text_chunk("Fixed answer."),
            _agent_update(AIMessage(content="Fixed answer.")),
        ]
    )

    statuses = _statuses(events)
    assert statuses == [
        "Analyzing question",
        "Running analytical query",
        "Refining query after an execution error",
        "Running analytical query",
        "Checking query result",
        "Preparing final answer",
    ]
    assert _answer(events) == "Fixed answer."


def test_consecutive_duplicate_statuses_are_deduplicated():
    events = _collect(
        [
            _agent_update(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_sql",
                            "args": {"sql": "a"},
                            "id": "r1",
                            "type": "tool_call",
                        },
                        {
                            "name": "run_sql",
                            "args": {"sql": "b"},
                            "id": "r2",
                            "type": "tool_call",
                        },
                    ],
                )
            ),
            _tools_update(
                _tool_result("run_sql", {"ok": True}, "r1"),
                _tool_result("run_sql", {"ok": True}, "r2"),
            ),
            _text_chunk("answer"),
            _agent_update(AIMessage(content="answer")),
        ]
    )

    statuses = _statuses(events)
    assert statuses.count("Running analytical query") == 1
    assert statuses.count("Checking query result") == 1
    # Order is preserved even though duplicates are collapsed.
    assert statuses == [
        "Analyzing question",
        "Running analytical query",
        "Checking query result",
        "Preparing final answer",
    ]


# ---------------------------------------------------------------------------
# Answer streaming and reasoning privacy
# ---------------------------------------------------------------------------


def test_answer_deltas_reconstruct_the_final_answer():
    events = _collect(
        [
            _text_chunk("There were "),
            _text_chunk("95,xxx "),
            _text_chunk("delivered orders."),
            _agent_update(AIMessage(content="There were 95,xxx delivered orders.")),
        ]
    )

    assert _answer(events) == "There were 95,xxx delivered orders."
    assert _types(events).count("answer_delta") == 3


def test_reasoning_content_is_never_streamed():
    reasoning_chunk = AIMessageChunk(
        content=[
            {"type": "reasoning", "reasoning": "SECRET_CHAIN_OF_THOUGHT"},
            {"type": "text", "text": "visible answer"},
        ]
    )
    events = _collect(
        [
            _chunk(reasoning_chunk),
            _agent_update(AIMessage(content="visible answer")),
        ]
    )

    assert _answer(events) == "visible answer"
    serialized = json.dumps(events)
    assert "SECRET_CHAIN_OF_THOUGHT" not in serialized


def test_tool_turn_narration_does_not_leak_into_answer_deltas():
    # Regression for real DeepSeek behavior: narration is emitted *before* the
    # tool_call_chunk in the same agent turn. It must never reach answer_delta.
    narration = "I'll inspect the schema first."
    events = _collect(
        [
            _text_chunk(narration),
            _chunk(
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "run_sql",
                            "args": "",
                            "id": "r1",
                            "index": 0,
                            "type": "tool_call_chunk",
                        }
                    ],
                )
            ),
            _agent_update(_tool_call("run_sql", "r1")),
            _tools_update(_tool_result("run_sql", {"ok": True}, "r1")),
            _text_chunk("the final answer"),
            _agent_update(AIMessage(content="the final answer")),
        ]
    )

    deltas = [e["text"] for e in events if e["type"] == "answer_delta"]
    assert deltas == ["the final answer"]
    assert narration not in "".join(deltas)
    assert narration not in json.dumps(events)
    assert _answer(events) == "the final answer"
    assert _types(events)[-1] == "done"


# ---------------------------------------------------------------------------
# Terminal events and cancellation
# ---------------------------------------------------------------------------


def test_no_answer_produces_error_not_done():
    events = _collect(
        [
            _agent_update(_tool_call("get_schema", "g1")),
            _tools_update(_tool_result("get_schema", {"tables": []}, "g1")),
        ]
    )

    assert _types(events) == [
        "status",
        "status",
        "error",
    ]
    assert events[-1]["type"] == "error"
    assert "done" not in _types(events)


def test_done_after_answer():
    events = _collect(
        [
            _text_chunk("answer"),
            _agent_update(AIMessage(content="answer")),
        ]
    )
    assert _types(events) == ["status", "status", "answer_delta", "done"]


def test_generator_close_is_clean():
    async def consume_one_then_close() -> dict:
        events = _aiter([_text_chunk("answer")])
        generator = translate_agent_events(events)
        first = await generator.__anext__()
        await generator.aclose()
        return first

    first = asyncio.run(consume_one_then_close())
    assert first == {"type": "status", "message": "Analyzing question"}
