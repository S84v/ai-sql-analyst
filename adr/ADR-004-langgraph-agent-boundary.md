# ADR-004: LangGraph agent boundary

## Status

Accepted

## Context

ADR-003 exposed the framework-neutral database core to a model as two LangChain
tools (`get_schema`, `run_sql`) and explicitly deferred the agent graph, model
provider, and tool-exception handling to a later milestone. The database core
(`schema.py`, `query.py`) and the tool adapter (`tools.py`) are stable fixed
inputs.

This milestone finalizes the first orchestration boundary: a minimal
tool-calling loop that lets a model inspect the schema, run read-only SQL, see
the results, and retry on failure before answering. The loop must stay
provider-neutral (no model/provider construction here), keep the read-only
safety envelope owned by the core, and not grow speculative state,
persistence, or multi-agent machinery.

At the time of this decision the installed environment is `langgraph 1.2.12`
(with `langgraph-prebuilt 1.1.0`) and `langchain-core 1.6.6`. The relevant APIs
were verified in the project environment: `MessagesState`, `StateGraph`,
`START`, `END`, `ToolNode`, and `tools_condition` are available, and
`tools_condition` routes a last message with tool calls to `"tools"` and
otherwise to `"__end__"`.

## Decision

- **Introduce a single graph module, `backend/src/ai_sql_analyst/agent.py`.** It
  defines the graph and system policy only; the core modules and `tools.py` are
  unchanged.
- **The graph is exactly `START -> agent -> tools_condition -> {tools -> agent |
  END}`.** The `agent` node calls the tool-bound model; the `tools` node is
  `ToolNode` over the two existing tools.
- **State is `MessagesState` only.** No custom fields for question, schema, SQL,
  results, or answer, and no iteration counters or other speculative state.
  Tool interactions persist in the message history.
- **The model is a caller-supplied `BaseChatModel`:** `build_agent(model)`
  receives it and calls `bind_tools` on the two existing tools once at build
  time. `agent.py` never constructs OpenAI/DeepSeek/provider clients; provider
  and API construction are a later milestone.
- **Exactly `get_schema_tool` and `run_sql_tool` are bound**, preserving the
  fixed tool schemas from ADR-003. Only `sql: str` is model-controlled;
  `max_rows`, `timeout_ms`, and `conn` stay application-owned.
- **The system prompt is prepended, not stored.** Each turn sends
  `[SystemMessage(SYSTEM_PROMPT), *state["messages"]]`, so the policy is always
  applied and never duplicated in the persisted history. The policy requires
  database evidence before answering, schema inspection before generating SQL,
  tool results as the source of truth, no invented numbers, revision/retry on
  SQL errors, and final answers only with sufficient evidence.
- **`ToolNode` and `tools_condition` are used unmodified.** Routing is not
  hand-rolled; there was no concrete incompatibility with the stock utilities.
- **Structured SQL/tool errors stay visible to the model.** `run_sql_tool`
  already returns `{ok: false, error: {kind, message}}`; `ToolNode` serializes
  the dict into the `ToolMessage` content (verified: JSON string), so the model
  can read the error and try again.
- **Unexpected infrastructure exceptions are not swallowed.** The default
  `ToolNode` error handling converts only `ToolInvocationError` and re-raises
  everything else, so a database outage aborts the run rather than being
  silently hidden from the operator.
- **Loop termination uses LangGraph's normal execution limit.** There is no
  application-level assumption about a specific recursion-limit value and no
  custom iteration machinery; callers may pass the standard `recursion_limit`
  config if they need to bound a run.
- **Production supplies an explicit execution backstop at the HTTP boundary.**
  `api.py` passes `recursion_limit=25` to `agent.astream`, matching the
  evaluation runner's budget and the current LangGraph default
  (`DEFAULT_RECURSION_LIMIT = 25`). The agent module stays
  provider/configuration-neutral — it still makes no assumption about a specific
  value — because the bound is owned by the caller, not by `agent.py`.

## Alternatives considered

- **`create_react_agent` / `create_agent` prebuilt.** Rejected: it hides the
  state schema, routing, and error-handling boundary the project wants to own,
  and would couple the decision to a higher-level abstraction with implicit
  defaults.
- **Custom agent state fields (question, schema, SQL, results, answer).**
  Rejected: it duplicates information already carried by messages, adds
  synchronization burden, and has no concrete requirement.
- **Hand-rolled routing instead of `tools_condition`.** Rejected: the stock
  utility was verified available and sufficient; custom routing would be
  speculative.
- **Overriding `handle_tool_errors` to swallow all exceptions.** Rejected: it
  would hide infrastructure failures from the operator and contradict the
  reliability goal.
- **Letting the graph construct the model/provider.** Rejected: it would make
  the graph non-neutral and push provider credentials/configuration into this
  milestone.
- **Checkpointing, memory, persistence, streaming, async, or multiple agents.**
  Rejected/deferred: none are required for a single-question read-only loop and
  each adds infrastructure the milestone explicitly excludes.
- **Custom recursion/iteration counter.** Rejected: LangGraph already bounds
  execution; a counter would be application-level duplication.

## Consequences

- The project gains one runtime dependency (`langgraph`), confined to
  `agent.py`; the database core and tools remain independently unit-testable.
- The graph is provider-neutral and synchronous, with a single source of state
  (messages) and stock routing/execution utilities.
- All model-visible database access continues to flow through the ADR-002 and
  ADR-003 boundaries; the graph cannot weaken the read-only safety envelope.
- Database outages propagate out of the graph instead of being returned to the
  model. Surfacing them as structured tool results would require changing
  `tools.py` and is deferred to a future milestone.
- `langchain-core`'s fake chat models do not implement `bind_tools`, so
  deterministic tests use a small test-only subclass of `GenericFakeChatModel`
  that records the binding and input. This is a test concern only.
- Later provider integration, the HTTP API, MCP, and any persistence layer are
  downstream consumers of `build_agent(model)`.
