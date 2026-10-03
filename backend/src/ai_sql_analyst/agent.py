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

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
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
    ``ToolMessage`` and remain visible to the model.
    """
    bound_model = model.bind_tools(list(TOOLS))

    builder = StateGraph(MessagesState)
    builder.add_node("agent", _agent_node(bound_model))
    builder.add_node("tools", ToolNode(list(TOOLS)))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    builder.add_edge("tools", "agent")
    return builder.compile()
