"""Provider-neutral LangGraph agent boundary (ADR-004).

Builds the smallest tool-calling loop over the two existing read-only tools
(ADR-003): an ``agent`` node invokes a caller-supplied chat model, and a
``tools`` node (``ToolNode``) executes tool calls until the model answers
without one. ``MessagesState`` is the only state; every tool interaction is
persisted in the message history.

This module does not construct model/provider clients. The caller passes a
``BaseChatModel``, keeping the graph provider-neutral for a later integration
milestone.
"""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ai_sql_analyst.tools import get_schema_tool, run_sql_tool

# The two tools are fixed inputs to this graph (ADR-003); the graph must expose
# exactly these and nothing else.
TOOLS = (get_schema_tool, run_sql_tool)

# Initial agent policy. It is prepended to the model input on every turn rather
# than stored in state, so it is always applied and never duplicated in the
# persisted message history.
SYSTEM_PROMPT = """You are a PostgreSQL analyst. Answer questions using data \
from the connected database, never from memory.

- Before writing SQL, call get_schema to inspect the tables, columns, and \
documented comments.
- Base every claim, especially numbers, on results returned by run_sql. Never \
invent or estimate results.
- Treat successful tool results as the source of truth. If a result is \
truncated, narrow the query.
- If run_sql returns an error, fix the error without changing what is being \
measured: keep the original scope, filters, grouping grain, aggregation, and \
denominator. Do not silently redefine the query to make it work.
- If run_sql reports a timeout, the computation may exceed the execution budget. \
You may try a materially cheaper, semantics-preserving reformulation, but do not \
keep retrying expensive variants. A LIMIT does not make an upstream expensive \
join or pairwise computation cheaper. After repeated timeouts, stop and say the \
computation is not feasible within the budget instead of estimating a result or \
silently changing the requested calculation.
- Preserve the scope the user asked for. Do not replace all matching rows with \
a sample, all requested results with a top-N, or a complete result with a \
preview. If a result is genuinely too large, you may present a limited subset \
only if you clearly say so and do not imply it is the complete result.
- Stop querying and produce the final answer once the tool results directly \
answer the question; continue only if a real ambiguity, an execution error, or \
missing evidence remains.
- Every substantive claim must be supported by tool results from this run. Do \
not add comparisons, trends, relationships, causes, or other conclusions that \
would require another query, and do not present a hypothesis as established \
fact.
"""


# Hardening: a run_sql timeout means the requested computation may be
# impractical within the execution budget. The agent may recover from early
# timeouts, but once this many total timeouts are observed in one request the
# loop stops with a deterministic, grounded answer instead of retrying until
# LangGraph's recursion limit. The count is derived from the persisted tool
# messages -- ADR-004 keeps MessagesState as the single source of state -- so a
# successful query between timeouts does not reset it (ADR-009).
TIMEOUT_BUDGET = 3

# The terminal node name is shared with the streaming translator (ADR-006) and
# the observability run-outcome tap (ADR-010); define it once so those modules
# never duplicate the literal. Renaming the node is a graph change.
TIMEOUT_STOP_NODE = "timeout_stop"

# Deterministic, grounded terminal answer. It states the observed failure
# (repeated timeouts) and explicitly refuses to estimate or redefine the
# requested calculation; it introduces no numbers that could be read as a
# fabricated result.
TIMEOUT_STOP_MESSAGE = (
    "The requested analysis could not be completed within the database "
    "execution time limit after multiple query attempts. No estimate or partial "
    "result is being presented as the complete answer. Try narrowing the "
    "question or adding filters and run it again."
)


def _timeout_failures(messages: list[BaseMessage]) -> int:
    """Count the total run_sql timeout failures recorded in one request.

    The count is read from the persisted ``ToolMessage`` history rather than a
    separate state field, so state stays ``MessagesState``-only (ADR-004/009).
    It is a total, not a streak: a successful query does not reset it.
    """
    count = 0
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "run_sql":
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("kind") == "timeout":
                count += 1
    return count


def _route_after_tools(state: MessagesState) -> str:
    """Route to the terminal node once the timeout budget is exhausted."""
    if _timeout_failures(state["messages"]) >= TIMEOUT_BUDGET:
        return "stop"
    return "agent"


def _timeout_stop_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
    """Emit the deterministic terminal answer without another model turn."""
    return {"messages": [AIMessage(content=TIMEOUT_STOP_MESSAGE)]}


def _agent_node(bound_model):
    """Return the node that calls the tool-bound model once per turn."""

    def agent(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [bound_model.invoke(messages)]}

    return agent


def build_agent(model: BaseChatModel) -> CompiledStateGraph:
    """Compile the provider-neutral tool-calling graph for ``model``.

    The model is bound to the two existing tools once at build time. Routing
    uses ``tools_condition`` unmodified: it sends turns with tool calls to the
    ``tools`` node and ends the graph otherwise. Unexpected tool exceptions are
    not swallowed: ``ToolNode``'s default error handling only converts
    ``ToolInvocationError`` and re-raises everything else. SQL/validation
    failures do not raise -- they arrive as structured results in the tool's
    ``ToolMessage`` and remain visible to the model. SQL timeouts are
    additionally bounded: after ``TIMEOUT_BUDGET`` total timeouts the ``tools``
    node routes to a deterministic terminal node instead of the model (ADR-009).
    """
    bound_model = model.bind_tools(list(TOOLS))

    builder = StateGraph(MessagesState)
    builder.add_node("agent", _agent_node(bound_model))
    builder.add_node("tools", ToolNode(list(TOOLS)))
    builder.add_node(TIMEOUT_STOP_NODE, _timeout_stop_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    builder.add_conditional_edges(
        "tools",
        _route_after_tools,
        {"agent": "agent", "stop": TIMEOUT_STOP_NODE},
    )
    builder.add_edge(TIMEOUT_STOP_NODE, END)
    return builder.compile()
