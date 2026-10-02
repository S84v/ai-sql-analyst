"""Tests for the provider-neutral LangGraph agent boundary (ADR-004).

Deterministic and database-free. ``langchain-core``'s fake chat models do not
implement ``bind_tools`` in this version, so ``_ScriptedChatModel`` is the
smallest test-only subclass that records the binding and the model input while
reusing ``GenericFakeChatModel`` to script replies. The real database tools are
never called: the schema/query functions behind ``tools.py`` are monkeypatched,
except where a validation failure is exercised before any connection attempt.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from pydantic import Field

from ai_sql_analyst import tools as tools_module
from ai_sql_analyst.agent import SYSTEM_PROMPT, build_agent
from ai_sql_analyst.query import SqlResult
from ai_sql_analyst.schema import ColumnSchema, DatabaseSchema, TableSchema
from ai_sql_analyst.tools import get_schema_tool, run_sql_tool


class _ScriptedChatModel(GenericFakeChatModel):
    """Scripted fake model that supports the graph's ``bind_tools`` boundary.

    ``GenericFakeChatModel`` ignores tool schemas and returns messages in order,
    which is what makes the graph deterministic; this subclass only records what
    was bound and what the model received.
    """

    bound_tools: list[BaseTool] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        self.bound_tools = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        self.seen.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def _scripted(*messages: BaseMessage) -> _ScriptedChatModel:
    return _ScriptedChatModel(messages=iter(messages))


def _tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _fake_schema() -> DatabaseSchema:
    return DatabaseSchema(
        tables=(
            TableSchema(
                name="t",
                comment=None,
                columns=(
                    ColumnSchema(
                        name="c", data_type="text", nullable=False, comment=None
                    ),
                ),
                primary_key=("c",),
                foreign_keys=(),
                checks=(),
            ),
        )
    )


def _fake_result() -> SqlResult:
    return SqlResult(
        ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
    )


def _run(model: _ScriptedChatModel, question: str = "How many orders are there?"):
    graph = build_agent(model)
    return graph, graph.invoke({"messages": [HumanMessage(content=question)]})


def _tool_messages(result: dict) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


# ---------------------------------------------------------------------------
# Construction and binding
# ---------------------------------------------------------------------------


def test_build_agent_binds_exactly_the_existing_tools():
    model = _scripted(AIMessage(content="done"))
    build_agent(model)

    assert len(model.bound_tools) == 2
    assert model.bound_tools[0] is get_schema_tool
    assert model.bound_tools[1] is run_sql_tool
    assert [t.name for t in model.bound_tools] == ["get_schema", "run_sql"]


def test_graph_structure_has_agent_and_tools_nodes():
    model = _scripted(AIMessage(content="done"))
    graph = build_agent(model)

    node_names = set(graph.get_graph().nodes)
    assert {"agent", "tools"} <= node_names


# ---------------------------------------------------------------------------
# Routing and message history
# ---------------------------------------------------------------------------


def test_direct_answer_without_tool_calls_ends_the_graph():
    model = _scripted(AIMessage(content="No database evidence was needed."))
    _, result = _run(model)

    assert [type(m).__name__ for m in result["messages"]] == [
        "HumanMessage",
        "AIMessage",
    ]
    assert _tool_messages(result) == []
    assert result["messages"][-1].content == "No database evidence was needed."


def test_system_prompt_is_prepended_to_the_model_input():
    model = _scripted(AIMessage(content="done"))
    _run(model, question="How many orders?")

    assert len(model.seen) == 1
    first_input = model.seen[0]
    assert isinstance(first_input[0], SystemMessage)
    assert first_input[0].content == SYSTEM_PROMPT
    assert isinstance(first_input[1], HumanMessage)
    assert first_input[1].content == "How many orders?"


def test_get_schema_call_round_trips_structured_tool_result(monkeypatch):
    fake = _fake_schema()
    monkeypatch.setattr(tools_module, "get_schema", lambda: fake)
    model = _scripted(
        _tool_call("get_schema", {}, "call-schema"),
        AIMessage(content="The table has column c."),
    )
    _, result = _run(model)

    received = _tool_messages(result)
    assert len(received) == 1
    assert received[0].tool_call_id == "call-schema"
    assert json.loads(received[0].content) == fake.to_dict()
    assert result["messages"][-1].content == "The table has column c."


def test_run_sql_call_round_trips_structured_tool_result(monkeypatch):
    fake = _fake_result()
    calls: list[str] = []

    def fake_run_sql(sql: str) -> SqlResult:
        calls.append(sql)
        return fake

    monkeypatch.setattr(tools_module, "run_sql", fake_run_sql)
    model = _scripted(
        _tool_call("run_sql", {"sql": "SELECT 1 AS x"}, "call-sql"),
        AIMessage(content="The answer is 1."),
    )
    _, result = _run(model)

    received = _tool_messages(result)
    assert len(received) == 1
    assert received[0].tool_call_id == "call-sql"
    assert json.loads(received[0].content) == fake.to_dict()
    assert calls == ["SELECT 1 AS x"]


def test_multiple_tool_calls_in_one_turn_all_execute(monkeypatch):
    monkeypatch.setattr(tools_module, "get_schema", _fake_schema)
    monkeypatch.setattr(tools_module, "run_sql", lambda sql: _fake_result())
    model = _scripted(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_schema", "args": {}, "id": "call-a", "type": "tool_call"},
                {
                    "name": "run_sql",
                    "args": {"sql": "SELECT 1 AS x"},
                    "id": "call-b",
                    "type": "tool_call",
                },
            ],
        ),
        AIMessage(content="Done."),
    )
    _, result = _run(model)

    received = _tool_messages(result)
    assert len(received) == 2
    assert {m.tool_call_id for m in received} == {"call-a", "call-b"}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_validation_error_is_visible_and_loop_continues():
    # "DROP TABLE x" fails lexical validation before any connection attempt, so
    # the real run_sql path is DB-free here.
    model = _scripted(
        _tool_call("run_sql", {"sql": "DROP TABLE x"}, "call-bad"),
        AIMessage(content="That statement was rejected; I revised it."),
    )
    _, result = _run(model)

    received = _tool_messages(result)
    assert len(received) == 1
    payload = json.loads(received[0].content)
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "validation"
    assert result["messages"][-1].content == "That statement was rejected; I revised it."


def test_unexpected_tool_exception_propagates(monkeypatch):
    def boom() -> DatabaseSchema:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tools_module, "get_schema", boom)
    model = _scripted(_tool_call("get_schema", {}, "call-boom"))
    graph = build_agent(model)

    with pytest.raises(RuntimeError, match="database unavailable"):
        graph.invoke({"messages": [HumanMessage(content="What tables exist?")]})


# ---------------------------------------------------------------------------
# Live-database tests (opt-in; CI has no database)
# ---------------------------------------------------------------------------


def test_live_agent_uses_real_tools():
    import os

    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    model = _scripted(
        _tool_call("run_sql", {"sql": "SELECT 1 AS x"}, "call-live"),
        AIMessage(content="The answer is 1."),
    )
    _, result = _run(model)

    received = _tool_messages(result)
    assert len(received) == 1
    payload = json.loads(received[0].content)
    assert payload["ok"] is True
    assert payload["rows"] == [[1]]
