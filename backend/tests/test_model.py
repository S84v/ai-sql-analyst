"""Tests for the DeepSeek provider boundary (ADR-005).

Deterministic and API-free: ``build_model`` only constructs a client, it never
makes a network call, and the request payload can be inspected directly. Tests
that reach into private ``langchain-openai`` methods (``_get_request_payload``,
``_use_responses_api``, ``bind_tools().kwargs``) are isolated in a clearly marked
section because they verify the exact wire contract that DeepSeek's Responses API
requires; they are version-coupled to ``langchain-openai==1.6.7``.

A developer's repo-root ``.env`` is neutralised in deterministic tests so a real
key can never leak in or influence assertions.
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ai_sql_analyst import model as model_module
from ai_sql_analyst.agent import build_agent
from ai_sql_analyst.model import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REASONING_EFFORT,
    build_model,
)
from ai_sql_analyst.tools import get_schema_tool, run_sql_tool

DUMMY_API_KEY = "sk-test-not-a-real-key"


def _isolate_env(monkeypatch) -> None:
    """Stop ``build_model`` from reading a developer's repo-root ``.env``."""
    monkeypatch.setattr(model_module, "load_dotenv", lambda *args, **kwargs: None)


@pytest.fixture
def model(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, DUMMY_API_KEY)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return build_model()


# ---------------------------------------------------------------------------
# Configuration contract (public attributes)
# ---------------------------------------------------------------------------


def test_build_model_uses_deepseek_flash(model):
    assert model.model_name == "deepseek-flash"
    assert model.model_name == DEEPSEEK_MODEL


def test_build_model_uses_deepseek_base_url(model):
    assert model.openai_api_base == "https://api.deepseek.com"
    assert model.openai_api_base == DEEPSEEK_BASE_URL


def test_build_model_explicitly_enables_responses_api(model):
    assert model.use_responses_api is True


def test_build_model_sets_responses_output_version(model):
    assert model.output_version == "responses/v1"


def test_build_model_sets_reasoning_effort_high(model):
    assert model.reasoning == {"effort": "high"}
    assert model.reasoning == {"effort": DEEPSEEK_REASONING_EFFORT}


def test_build_model_sources_api_key_from_deepseek_env(model):
    assert model.openai_api_key.get_secret_value() == DUMMY_API_KEY


def test_build_model_does_not_fall_back_to_openai_api_key(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-must-not-be-used")

    with pytest.raises(RuntimeError, match=DEEPSEEK_API_KEY_ENV):
        build_model()


def test_build_model_missing_api_key_raises_clear_error(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        build_model()

    message = str(excinfo.value)
    assert DEEPSEEK_API_KEY_ENV in message
    # The error must name the variable, not echo any secret material.
    assert DUMMY_API_KEY not in message


def test_build_model_satisfies_build_agent(model):
    graph = build_agent(model)
    node_names = set(graph.get_graph().nodes)
    assert {"agent", "tools"} <= node_names


# ---------------------------------------------------------------------------
# Responses API wire contract.
#
# These tests inspect private ``langchain-openai`` methods on purpose: they pin
# the exact request shape that DeepSeek's stateless Responses API must receive.
# They are coupled to ``langchain-openai==1.6.7`` (see the module docstring).
# ---------------------------------------------------------------------------


def _request_payload(model, messages, **kwargs):
    return model._get_request_payload(messages, **kwargs)


def test_bind_tools_produces_responses_function_tools(model):
    bound = model.bind_tools([get_schema_tool, run_sql_tool])
    payload = _request_payload(model, [HumanMessage("hi")], tools=bound.kwargs["tools"])

    assert "messages" not in payload
    assert "input" in payload
    tools_by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert set(tools_by_name) == {"get_schema", "run_sql"}
    for tool in tools_by_name.values():
        assert tool["type"] == "function"
        # Responses shape is flat; Chat Completions nests under "function".
        assert "function" not in tool
    assert tools_by_name["run_sql"]["parameters"]["required"] == ["sql"]
    assert payload["reasoning"] == {"effort": "high"}


def test_tool_call_round_trip_uses_responses_function_items(model):
    messages = [
        HumanMessage("How many orders?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "run_sql",
                    "args": {"sql": "SELECT count(*) FROM orders"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content='{"ok": true, "rows": [[99441]]}', tool_call_id="call_1"),
    ]
    payload = _request_payload(model, messages)
    input_items = payload["input"]
    types = [item["type"] for item in input_items]

    assert "function_call" in types
    assert "function_call_output" in types
    function_call = next(i for i in input_items if i["type"] == "function_call")
    assert function_call["name"] == "run_sql"
    assert function_call["call_id"] == "call_1"
    function_output = next(i for i in input_items if i["type"] == "function_call_output")
    assert function_output["call_id"] == "call_1"
    assert function_output["output"] == '{"ok": true, "rows": [[99441]]}'


def _assistant_with_reasoning_and_tool_call() -> AIMessage:
    """An assistant turn shaped like DeepSeek's Responses output.

    DeepSeek returns a plain-text ``reasoning`` item before the ``function_call``.
    """
    return AIMessage(
        content=[
            {
                "type": "reasoning",
                "id": "rs_1",
                "status": "completed",
                "summary": [],
                "content": [
                    {"type": "reasoning_text", "text": "I need the order count."}
                ],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "run_sql",
                "arguments": '{"sql": "SELECT count(*) FROM orders"}',
            },
        ],
        tool_calls=[
            {
                "name": "run_sql",
                "args": {"sql": "SELECT count(*) FROM orders"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )


def test_reasoning_history_replays_across_the_tool_loop(model):
    messages = [
        HumanMessage("How many orders?"),
        _assistant_with_reasoning_and_tool_call(),
        ToolMessage(content='{"ok": true, "rows": [[99441]]}', tool_call_id="call_1"),
    ]
    payload = _request_payload(model, messages)
    input_items = payload["input"]
    types = [item["type"] for item in input_items]

    # DeepSeek requires the reasoning item to be replayed alongside the tool call.
    assert "reasoning" in types
    assert "function_call" in types
    assert "function_call_output" in types
    reasoning_item = next(i for i in input_items if i["type"] == "reasoning")
    assert reasoning_item["content"][0]["type"] == "reasoning_text"
    assert reasoning_item["content"][0]["text"] == "I need the order count."
    # Ordering matters: reasoning precedes its function call / output.
    assert (
        types.index("reasoning")
        < types.index("function_call")
        < types.index("function_call_output")
    )


def test_store_false_would_drop_reasoning_history(model):
    # Documents why model.py leaves `store` unset (ADR-005): DeepSeek needs the
    # reasoning history replayed, and `store=False` would discard it.
    messages = [
        _assistant_with_reasoning_and_tool_call(),
        ToolMessage(content="{}", tool_call_id="call_1"),
    ]
    payload = _request_payload(model, messages, store=False)
    types = [item["type"] for item in payload["input"]]

    assert "reasoning" not in types
    assert "function_call" in types


# ---------------------------------------------------------------------------
# Live provider smoke test (opt-in; never runs in CI)
# ---------------------------------------------------------------------------


def test_live_deepseek_tool_loop():
    if os.environ.get("RUN_MODEL_TESTS") != "1":
        pytest.skip("set RUN_MODEL_TESTS=1 to run the live DeepSeek test")
    try:
        # build_model() loads the repo-root .env, so a key configured there or in
        # the process environment both work; a missing key skips rather than errors.
        live_model = build_model()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    graph = build_agent(live_model)
    result = graph.invoke(
        {"messages": [HumanMessage(content="How many rows are in the orders table?")]}
    )

    messages = result["messages"]
    assert any(isinstance(m, ToolMessage) for m in messages), "expected a tool call"
    final = messages[-1]
    assert isinstance(final, AIMessage)
    assert str(final.content).strip(), "expected a non-empty final answer"
