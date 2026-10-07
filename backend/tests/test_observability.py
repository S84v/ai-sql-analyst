"""Deterministic tests for the OpenTelemetry observability boundary (ADR-010).

No telemetry backend, no network exporter, no DeepSeek key, and no PostgreSQL:
in-memory span/metric providers are installed once for the module, and the SQL
path is monkeypatched. The tests assert structure and outcome metadata, not
content -- content must never be captured.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from starlette.testclient import TestClient

from ai_sql_analyst import observability
from ai_sql_analyst import query as query_module
from ai_sql_analyst.agent import build_agent
from ai_sql_analyst.api import create_app
from ai_sql_analyst.query import SqlErrorKind, SqlResult

# ---------------------------------------------------------------------------
# Shared in-memory OpenTelemetry providers (one process-wide setup)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def otel():
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    observability.configure(
        tracer_provider=tracer_provider, meter_provider=meter_provider
    )
    yield SimpleNamespace(
        span_exporter=span_exporter,
        metric_reader=metric_reader,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    tracer_provider.shutdown()
    meter_provider.shutdown()


def _run_sql_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == "run_sql"]


def _metric_points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    points: list[Any] = []
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


# ---------------------------------------------------------------------------
# SQL instrumentation (query.py)
# ---------------------------------------------------------------------------


def test_run_sql_success_records_span_and_metrics(otel, monkeypatch):
    otel.span_exporter.clear()
    monkeypatch.setattr(
        query_module,
        "_execute_query",
        lambda *a, **k: SqlResult(
            ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
        ),
    )

    result = query_module.run_sql("SELECT 1 AS x", conn=object())

    assert result.ok is True
    spans = _run_sql_spans(otel.span_exporter)
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes["db.system"] == "postgresql"
    assert attributes["db.operation"] == "SELECT"
    assert attributes["olistiq.sql.outcome"] == "ok"
    assert attributes["olistiq.sql.truncated"] is False
    assert attributes["olistiq.sql.row_count"] == 1

    executions = _metric_points(otel.metric_reader, "olistiq.sql.executions")
    assert any(
        p.attributes.get("outcome") == "ok" and p.value >= 1 for p in executions
    )
    durations = _metric_points(otel.metric_reader, "olistiq.sql.duration")
    assert any(p.attributes.get("outcome") == "ok" for p in durations)


class _TimeoutError(psycopg.Error):
    # A stand-in with the query_canceled SQLSTATE so _classify maps it to timeout.
    sqlstate = "57014"


def test_run_sql_outcomes_are_distinguishable(otel, monkeypatch):
    cases = [
        (
            "validation",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not execute")),
            "DROP TABLE x",
        ),
        (
            "timeout",
            lambda *a, **k: (_ for _ in ()).throw(_TimeoutError("canceling statement")),
            "SELECT pg_sleep(3)",
        ),
        (
            "execution",
            lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("boom")),
            "SELECT 1",
        ),
        (
            "unexpected",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unexpected")),
            "SELECT 1",
        ),
    ]

    for expected, behaviour, sql in cases:
        otel.span_exporter.clear()
        monkeypatch.setattr(query_module, "_execute_query", behaviour)

        result = query_module.run_sql(sql, conn=object())

        assert result.ok is False
        spans = _run_sql_spans(otel.span_exporter)
        assert len(spans) == 1, expected
        assert spans[0].attributes["olistiq.sql.outcome"] == expected

        executions = _metric_points(otel.metric_reader, "olistiq.sql.executions")
        assert any(p.attributes.get("outcome") == expected for p in executions)


def test_parenthesized_sql_records_no_db_operation(otel, monkeypatch):
    otel.span_exporter.clear()
    monkeypatch.setattr(
        query_module,
        "_execute_query",
        lambda *a, **k: SqlResult(
            ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
        ),
    )

    query_module.run_sql("(SELECT 1)", conn=object())

    attributes = _run_sql_spans(otel.span_exporter)[0].attributes
    assert "db.operation" not in attributes
    assert attributes["olistiq.sql.outcome"] == "ok"


def test_sql_telemetry_excludes_sensitive_content(otel, monkeypatch):
    otel.span_exporter.clear()
    sql = "SELECT secret_column FROM secret_table"
    monkeypatch.setattr(
        query_module,
        "_execute_query",
        lambda *a, **k: SqlResult(
            ok=True,
            columns=("ssn",),
            rows=(("123-45-6789",),),
            row_count=1,
            truncated=False,
        ),
    )

    query_module.run_sql(sql, conn=object())

    blob = json.dumps(
        [
            {key: str(value) for key, value in (span.attributes or {}).items()}
            for span in otel.span_exporter.get_finished_spans()
        ]
    )
    for secret in ("secret_column", "secret_table", "123-45-6789", "ssn"):
        assert secret not in blob


# ---------------------------------------------------------------------------
# /query run-outcome classification (api.py + observability.py)
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self, events: list[dict] | None = None, error: Exception | None = None):
        self._events = events or []
        self._error = error

    async def astream(self, input_, config=None, stream_mode=None, version=None):
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
        _text_chunk("The answer is 42."),
        _agent_update(AIMessage(content="The answer is 42.")),
    ]


def _no_answer_events() -> list[dict]:
    return [
        _agent_update(_tool_call("get_schema", "g1")),
        _tools_update(_tool_result("get_schema", {"tables": []}, "g1")),
    ]


def _run_query(otel, agent: _FakeAgent) -> Any:
    otel.span_exporter.clear()
    app = create_app(
        agent_factory=lambda: agent,
        tracer_provider=otel.tracer_provider,
        meter_provider=otel.meter_provider,
    )
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "Any question?"})
        assert response.status_code == 200
    server_spans = [
        span
        for span in otel.span_exporter.get_finished_spans()
        if span.kind is SpanKind.SERVER
    ]
    assert server_spans, "official FastAPI instrumentation should emit a server span"
    return server_spans[-1]


def test_run_outcome_success(otel):
    span = _run_query(otel, _FakeAgent(_answer_events()))
    assert span.attributes["olistiq.run.outcome"] == observability.OUTCOME_SUCCESS


def test_run_outcome_no_answer(otel):
    span = _run_query(otel, _FakeAgent(_no_answer_events()))
    assert span.attributes["olistiq.run.outcome"] == observability.OUTCOME_NO_ANSWER


def test_run_outcome_timeout_stop_overrides_success(otel):
    events = [
        {"type": "updates", "data": {observability.TIMEOUT_STOP_NODE: {"messages": [AIMessage(content="terminal")]}}}
    ]
    span = _run_query(otel, _FakeAgent(events))
    assert span.attributes["olistiq.run.outcome"] == observability.OUTCOME_TIMEOUT_STOP


def test_run_outcome_recursion_limit(otel):
    span = _run_query(otel, _FakeAgent(error=GraphRecursionError("limit")))
    assert span.attributes["olistiq.run.outcome"] == observability.OUTCOME_RECURSION_LIMIT


def test_run_outcome_unexpected_error(otel):
    span = _run_query(otel, _FakeAgent(error=RuntimeError("boom")))
    assert span.attributes["olistiq.run.outcome"] == observability.OUTCOME_ERROR


# ---------------------------------------------------------------------------
# Official instrumentation activation
# ---------------------------------------------------------------------------


def test_fastapi_instrumentation_activates(otel):
    app = create_app(
        agent_factory=lambda: _FakeAgent(_answer_events()),
        tracer_provider=otel.tracer_provider,
        meter_provider=otel.meter_provider,
    )
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


def test_langchain_instrumentation_activates_without_langchain_meta_package(otel):
    import importlib.util

    # The upstream instrumentor's distribution check targets the `langchain`
    # meta-package, which this project intentionally does not install.
    assert importlib.util.find_spec("langchain") is None
    assert LangChainInstrumentor().is_instrumented_by_opentelemetry is True


class _ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def test_content_capture_is_disabled():
    # Privacy is pinned explicitly rather than relying on a library default.
    assert (
        os.environ.get(observability.GENAI_CAPTURE_CONTENT_ENV)
        == observability.GENAI_NO_CONTENT
    )


def test_content_capture_cannot_be_weakened_externally(monkeypatch):
    # An externally pre-set capture mode must be overridden, not honored.
    monkeypatch.setenv(observability.GENAI_CAPTURE_CONTENT_ENV, "SPAN_ONLY")

    observability.configure()

    assert (
        os.environ[observability.GENAI_CAPTURE_CONTENT_ENV]
        == observability.GENAI_NO_CONTENT
    )


def test_fake_model_graph_produces_telemetry_without_content(otel):
    otel.span_exporter.clear()
    model = _ScriptedModel(  # type: ignore[arg-type]
        messages=iter([AIMessage(content="ANSWER_SECRET_XYZ")])
    )
    graph = build_agent(model)

    graph.invoke({"messages": [HumanMessage(content="QUESTION_SECRET_XYZ")]})

    spans = otel.span_exporter.get_finished_spans()
    assert spans
    blob = json.dumps(
        [{key: str(value) for key, value in (span.attributes or {}).items()} for span in spans]
    )
    assert "QUESTION_SECRET_XYZ" not in blob
    assert "ANSWER_SECRET_XYZ" not in blob


# ---------------------------------------------------------------------------
# Export configuration
# ---------------------------------------------------------------------------


def test_setup_is_inert_without_providers(monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setattr(observability, "_tracer_provider", None)
    monkeypatch.setattr(observability, "_meter_provider", None)
    monkeypatch.delenv(observability.OTEL_EXPORTER_OTLP_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(observability.OLISTIQ_OTEL_CONSOLE_ENV, raising=False)

    app = FastAPI()
    observability.setup(app)

    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is False


def test_export_disabled_by_default(monkeypatch):
    monkeypatch.delenv(observability.OTEL_EXPORTER_OTLP_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(observability.OLISTIQ_OTEL_CONSOLE_ENV, raising=False)

    assert observability._build_from_env() == (None, None, False)


def test_console_export_is_opt_in(monkeypatch):
    monkeypatch.delenv(observability.OTEL_EXPORTER_OTLP_ENDPOINT_ENV, raising=False)
    monkeypatch.setenv(observability.OLISTIQ_OTEL_CONSOLE_ENV, "1")

    tracer_provider, meter_provider, owns = observability._build_from_env()

    assert tracer_provider is not None
    assert meter_provider is not None
    assert owns is True
    tracer_provider.shutdown()
    meter_provider.shutdown()
