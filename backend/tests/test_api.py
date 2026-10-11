"""Deterministic HTTP-contract tests for the streaming /query endpoint (ADR-006).

A fake agent is injected via ``create_app(agent_factory=...)`` so the tests need
no DeepSeek credentials, no PostgreSQL, and no browser. The fake emits real
LangGraph v2-shaped events, so the endpoint and the translator both run.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from starlette.testclient import TestClient

from ai_sql_analyst import api
from ai_sql_analyst.api import create_app
from ai_sql_analyst.ratelimit import FixedWindowRateLimiter


class FakeAgent:
    """Minimal stand-in for a compiled LangGraph agent."""

    def __init__(self, events: list[dict] | None = None, error: Exception | None = None):
        self._events = events or []
        self._error = error
        self.calls = 0
        self.inputs: list[Any] = []
        self.config: Any = None
        self.stream_mode: Any = None
        self.version: Any = None

    async def astream(self, input_, config=None, stream_mode=None, version=None):
        self.calls += 1
        self.inputs.append(input_)
        self.config = config
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


def test_query_passes_explicit_recursion_limit_backstop():
    # The production HTTP boundary supplies the same explicit execution bound as
    # the evaluation runner (25, the current LangGraph default).
    assert api._RECURSION_LIMIT == 25

    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        _stream_lines(client)

    assert agent.config == {"recursion_limit": api._RECURSION_LIMIT}


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


# ---------------------------------------------------------------------------
# CORS (production browser -> Cloud Run, ADR-011)
# ---------------------------------------------------------------------------


def test_parse_allowed_origins():
    assert api._parse_allowed_origins(None) == []
    assert api._parse_allowed_origins("") == []
    assert api._parse_allowed_origins("   ") == []
    assert api._parse_allowed_origins("https://a.example , https://b.example,") == [
        "https://a.example",
        "https://b.example",
    ]


def test_parse_allowed_origins_rejects_wildcard():
    # '*' is invalid anywhere: alone, mixed with exact origins, or embedded.
    for raw in ("*", " * ", "https://olistiq.web.app,*", "https://*.example.com"):
        with pytest.raises(ValueError):
            api._parse_allowed_origins(raw)


def test_no_cors_configuration_adds_no_permissive_headers(monkeypatch):
    monkeypatch.delenv(api._CORS_ALLOWED_ORIGINS_ENV, raising=False)
    with TestClient(create_app(agent_factory=lambda: FakeAgent())) as client:
        response = client.get("/query", headers={"Origin": "https://anywhere.example"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin_preflight(monkeypatch):
    monkeypatch.setenv(
        api._CORS_ALLOWED_ORIGINS_ENV,
        "https://olistiq.web.app , https://custom.example.com",
    )
    with TestClient(create_app(agent_factory=lambda: FakeAgent())) as client:
        response = client.options(
            "/query",
            headers={
                "Origin": "https://olistiq.web.app",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://olistiq.web.app"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_cors_rejects_unconfigured_origin(monkeypatch):
    monkeypatch.setenv(api._CORS_ALLOWED_ORIGINS_ENV, "https://olistiq.web.app")
    with TestClient(create_app(agent_factory=lambda: FakeAgent())) as client:
        response = client.options(
            "/query",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# Rate limiting (ADR-012)
#
# The limiter is process-local and keyed on the rightmost X-Forwarded-For entry.
# These tests drive it through the HTTP boundary with TestClient.
# ---------------------------------------------------------------------------


class _Clock:
    """Deterministic monotonic clock for limiter tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _post(client: TestClient, forwarded_for: str | None = None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return client.post("/query", json={"question": "How many orders?"}, headers=headers)


def test_rate_limit_allows_ten_then_rejects():
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        allowed = [_post(client, "203.0.113.5").status_code for _ in range(10)]
        rejected = _post(client, "203.0.113.5")

    assert allowed == [200] * 10
    assert rejected.status_code == 429
    # The rejected request never invoked the agent.
    assert agent.calls == 10


def test_rate_limit_retry_after_reflects_window_reset():
    clock = _Clock()
    limiter = FixedWindowRateLimiter(clock=clock)
    agent = FakeAgent(_answer_events())
    app = create_app(agent_factory=lambda: agent, rate_limiter=limiter)
    with TestClient(app) as client:
        for _ in range(10):
            _post(client, "203.0.113.5")
        clock.advance(25)
        response = _post(client, "203.0.113.5")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "35"


def test_rate_limit_has_independent_counters_per_key():
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        for _ in range(10):
            assert _post(client, "203.0.113.5").status_code == 200
        # A different client still has its full budget.
        assert _post(client, "198.51.100.9").status_code == 200
        # The exhausted client remains rejected.
        assert _post(client, "203.0.113.5").status_code == 429

    assert agent.calls == 11


def test_rate_limit_429_keeps_cors_headers(monkeypatch):
    origin = "https://olistiq-prod-2026.web.app"
    monkeypatch.setenv(api._CORS_ALLOWED_ORIGINS_ENV, origin)
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        for _ in range(10):
            client.post(
                "/query",
                json={"question": "q"},
                headers={"X-Forwarded-For": "203.0.113.5", "Origin": origin},
            )
        response = client.post(
            "/query",
            json={"question": "q"},
            headers={"X-Forwarded-For": "203.0.113.5", "Origin": origin},
        )

    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == origin


def test_rate_limit_does_not_apply_to_preflight(monkeypatch):
    origin = "https://olistiq-prod-2026.web.app"
    monkeypatch.setenv(api._CORS_ALLOWED_ORIGINS_ENV, origin)
    agent = FakeAgent(_answer_events())
    with TestClient(create_app(agent_factory=lambda: agent)) as client:
        for _ in range(15):
            preflight = client.options(
                "/query",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert preflight.status_code == 200
        # The preflights did not consume the POST budget.
        assert _post(client, "203.0.113.5").status_code == 200

    assert agent.calls == 1
