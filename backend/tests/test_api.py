"""Deterministic HTTP-contract tests for the streaming /query endpoint (ADR-006).

A fake agent is injected via ``create_app(agent_factory=...)`` so the tests need
no DeepSeek credentials, no PostgreSQL, and no browser. The fake emits real
LangGraph v2-shaped events, so the endpoint and the translator both run.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from starlette.testclient import TestClient

from ai_sql_analyst import api
from ai_sql_analyst.api import create_app


class FakeAgent:
    """Minimal stand-in for a compiled LangGraph agent."""

    def __init__(self, events: list[dict] | None = None, error: Exception | None = None):
        self._events = events or []
        self._error = error
        self.calls = 0
        self.inputs: list[Any] = []
        self.stream_mode: Any = None
        self.version: Any = None

    async def astream(self, input_, stream_mode=None, version=None):
        self.calls += 1
        self.inputs.append(input_)
        self.stream_mode = stream_mode
        self.version = version
        if self._error is not None:
            raise self._error
        for event in self._events:
            yield event


def _agent_update(*messages: Any) -> dict:
    return {"type": "updates", "data": {"agent": {"messages": list(messages)}}}


def _tools_update(*messages: Any) -> dict:
    return {"type": "updates", "data": {"tools": {"messages": list(messages)}}}


def _text_chunk(text: str) -> dict:
    return {
        "type": "messages",
        "data": (AIMessageChunk(content=text), {"langgraph_node": "agent"}),
    }


def _tool_call(name: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": call_id, "type": "tool_call"}],
    )


def _tool_result(name: str, payload: dict, call_id: str = "c1") -> ToolMessage:
    return ToolMessage(content=json.dumps(payload), tool_call_id=call_id, name=name)


def _answer_events() -> list[dict]:
    return [
        _agent_update(_tool_call("get_schema", "g1")),
        _tools_update(_tool_result("get_schema", {"tables": []}, "g1")),
        _agent_update(_tool_call("run_sql", "r1")),
        _tools_update(_tool_result("run_sql", {"ok": True}, "r1")),
        _text_chunk("There were "),
        _text_chunk("95 orders."),
        _agent_update(AIMessage(content="There were 95 orders.")),
    ]


def _parse_sse(lines: list[str]) -> list[tuple[str | None, dict | None]]:
    events: list[tuple[str | None, dict | None]] = []
    name: str | None = None
    data: str | None = None

    def flush() -> None:
        nonlocal name, data
        if name is not None or data is not None:
            events.append((name, json.loads(data) if data else None))
        name, data = None, None

    for line in lines:
        if line == "":
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = line.split(":", 1)[1].strip()
    flush()
    return events


def _stream_lines(client: TestClient, question: str = "How many orders?") -> list[str]:
    with client.stream("POST", "/query", json={"question": question}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return [line for line in response.iter_lines() if line is not None]


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_query_rejects_missing_and_blank_question():
    agent = FakeAgent()
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        assert client.post("/query", json={}).status_code == 422
        assert client.post("/query", json={"question": "   "}).status_code == 422
    assert agent.calls == 0


# ---------------------------------------------------------------------------
# Streaming contract
# ---------------------------------------------------------------------------


def test_query_streams_status_answer_deltas_and_done():
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        lines = _stream_lines(client)

    events = _parse_sse(lines)
    names = [name for name, _ in events]
    assert names[0] == "status"
    assert names[-1] == "done"
    assert "answer_delta" in names

    statuses = [data["message"] for name, data in events if name == "status"]
    assert statuses == [
        "Analyzing question",
        "Inspecting database schema",
        "Running analytical query",
        "Checking query result",
        "Preparing final answer",
    ]
    answer = "".join(data["text"] for name, data in events if name == "answer_delta")
    assert answer == "There were 95 orders."
    assert agent.calls == 1


def test_query_model_receives_question_as_human_message():
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        _stream_lines(client, question="How many were delivered in 2018?")

    messages = agent.inputs[0]["messages"]
    assert len(messages) == 1
    assert messages[0].content == "How many were delivered in 2018?"


def test_agent_stream_uses_messages_and_updates_modes_with_version_v2():
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        _stream_lines(client)

    assert agent.stream_mode == ["messages", "updates"]
    assert agent.version == "v2"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_query_emits_generic_error_on_agent_failure():
    agent = FakeAgent(error=RuntimeError("boom internal detail"))
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        lines = _stream_lines(client)

    events = _parse_sse(lines)
    names = [name for name, _ in events]
    # The start status is emitted before the agent runs, then the failure.
    assert names == ["status", "error"]
    message = events[-1][1]["message"]
    assert message == "The analysis could not be completed."
    assert "boom" not in "".join(lines)


def test_query_emits_error_when_agent_produces_no_answer():
    agent = FakeAgent([_agent_update(_tool_call("get_schema", "g1"))])
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        lines = _stream_lines(client)

    events = _parse_sse(lines)
    names = [name for name, _ in events]
    assert names[-1] == "error"
    assert "done" not in names


# ---------------------------------------------------------------------------
# Lifecycle / reuse
# ---------------------------------------------------------------------------


def test_agent_is_built_once_and_reused_across_requests():
    created: list[FakeAgent] = []
    agent = FakeAgent(_answer_events())

    def factory() -> FakeAgent:
        created.append(agent)
        return agent

    with TestClient(create_app(agent_factory=factory)) as client:
        _stream_lines(client)
        _stream_lines(client)

    assert created == [agent]
    assert agent.calls == 2


def test_lifespan_builds_the_default_agent(monkeypatch):
    fake = FakeAgent()
    monkeypatch.setattr(api, "build_default_agent", lambda: fake)

    app = api.create_app()
    with TestClient(app):
        assert app.state.agent is fake
