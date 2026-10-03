# ADR-006: Streaming HTTP boundary

## Status

Accepted

## Context

The agent is implemented and provider-integrated (ADR-004, ADR-005): a
provider-neutral LangGraph graph (`build_agent(model)`) runs a tool loop over the
two read-only database tools, backed by DeepSeek `deepseek-flash` via the
Responses API. There is no application boundary yet.

The next milestone exposes `POST /query` and must stream the response in real
time so the future UI can show concise progress ("Inspecting database schema",
"Running analytical query", ...) as the agent works, and deliver the final answer
through the same stream once the final agent turn completes. Two constraints are
central:

1. **Never expose raw model reasoning.** DeepSeek's Responses API streams
   reasoning items (`response.reasoning_text.delta`) alongside output text and
   function calls. Reasoning and internal LangGraph state must never reach the
   client; the visible "thinking" experience must be application-level status
   derived from observable tool activity.
2. **Do not redesign the agent.** The graph, tools, SQL boundary, and provider
   construction are fixed inputs.

The concrete question: what is the smallest clean boundary that turns the
existing agent into safe real-time progress plus the final answer delivered over
the same stream, without coupling the graph to FastAPI?

## Decision

- **FastAPI + Starlette is the HTTP boundary.** A new `api.py` owns request
  validation, application lifespan, agent reuse, HTTP error handling, and the
  transport. It is a thin adapter.
- **The endpoint streams.** `POST /query` returns Server-Sent Events using
  FastAPI's first-party SSE support (`fastapi.sse.EventSourceResponse` and
  `ServerSentEvent`), which sets `text/event-stream`, `Cache-Control: no-cache`,
  `X-Accel-Buffering: no`, keepalive comments, and handles structured teardown.
  The endpoint is an async generator and works over POST.
- **A stable application event protocol.** Events are `status` (`message`),
  `answer_delta` (`text`), `done`, and `error` (`message`). The client
  reconstructs the answer by concatenating `answer_delta.text`. The frontend
  never sees LangGraph or DeepSeek structures.
- **Translation lives in `streaming.py`, separate from HTTP.** It consumes
  LangGraph v2 events from
  `graph.astream(..., stream_mode=["messages", "updates"])` and yields plain
  dicts. It imports no FastAPI and knows nothing about SSE.
- **Progress comes from observable tool activity, not reasoning.** `updates`
  events provide the agent's `AIMessage.tool_calls` (tool decisions) and the
  `ToolMessage`s (tool results, via `ToolMessage.name` and the structured
  `run_sql` `ok` flag). These map to concise statuses. Consecutive duplicate
  statuses are collapsed while preserving order.
- **Answer tokens use `AIMessageChunk.text` only.** This returns text content
  and excludes reasoning content blocks; raw `content`, `additional_kwargs`, and
  DeepSeek reasoning events are never read or emitted.
- **Answer text is buffered per agent turn; progress is real-time.** Status
  events are emitted as the agent works. Live evaluation showed the model can
  narrate ("I'll inspect the schema first...") and *then* call a tool in the same
  turn. That narration is not the final answer, so a turn's text is emitted as
  `answer_delta` only once the turn completes without tool calls; text from a
  tool-calling turn is discarded. The final answer is therefore delivered through
  the SSE stream after the final agent turn completes, not token-by-token. This
  keeps the client's reconstruction exact.
- **No graph change is required.** Verified in the installed environment that
  `stream_mode="messages"` emits token chunks even though the existing `agent`
  node calls the synchronous `bound_model.invoke`. The graph stays
  provider/transport-neutral; ADR-004 is unaffected.
- **Async endpoint, synchronous internals.** The endpoint is `async` and drives
  `agent.astream`; LangGraph runs the existing sync model and sync `psycopg`
  tools in a threadpool, so `model.py`, `tools.py`, and `query.py` are unchanged.
- **Lifecycle: build once, reuse.** FastAPI lifespan calls the injected
  `agent_factory` (default: `build_agent(build_model())`) once and stores the
  compiled graph on `app.state.agent`. Requests reuse it. There is no
  persistence, memory, checkpointing, session, or connection pool; each request
  is an independent single-question run, and the compiled graph is stateless and
  safe to reuse concurrently.
- **SQL errors stay in the agent loop.** A failed `run_sql` yields a
  `Refining query after an execution error` status and the loop continues; it is
  never an HTTP error. Only unexpected exceptions escaping the agent become a
  single generic `error` event (no internal details; logged server-side).
- **Database ownership is unchanged.** FastAPI owns no connection; access
  remains `agent -> tools -> query.py -> db.py`.

## Alternatives considered

- **`sse-starlette`.** Rejected: FastAPI now has first-party SSE
  (`EventSourceResponse`/`ServerSentEvent`), and the current `sse-starlette`
  release adds heavyweight transitive dependencies (SQLAlchemy, aiosqlite,
  granian, daphne) for no needed capability.
- **Manual SSE framing over Starlette `StreamingResponse`.** Rejected: the
  first-party support already handles framing, keepalives, headers, and
  cancellation teardown.
- **WebSockets.** Rejected: the flow is one request to one server-pushed stream;
  SSE is simpler and adequate.
- **Newline-delimited JSON (NDJSON).** Rejected: SSE is the conventional
  server-push framing and its event names map directly to the event protocol.
- **Emitting progress from `agent.py`/`tools.py` via LangGraph `custom` events.**
  Rejected: it would couple orchestration and tools to presentation and require
  changes to fixed modules. The needed signals are already observable via
  `updates`.
- **Exposing raw LangGraph events or DeepSeek reasoning to the client.**
  Rejected: leaks internal state and chain-of-thought; violates the privacy
  requirement.
- **Building the model/graph per request.** Rejected: needless repeated
  construction; lifespan reuse is simpler and sufficient.
- **Rewriting the model/SQL stack to async.** Rejected: the threadpool execution
  of sync nodes is sufficient and keeps the change small.

## Consequences

- The project gains a real HTTP boundary with one new runtime dependency pair
  (`fastapi`, `uvicorn`) and a dev dependency (`httpx2`, for Starlette's
  `TestClient`). `fastapi` brings a core `opentelemetry-api` dependency that is
  inert here.
- The graph, tools, SQL safety boundary, and provider integration are untouched;
  the boundary is optional and replaceable.
- Reasoning is structurally excluded: only `AIMessageChunk.text` is emitted, and
  DeepSeek's `reasoning_text` deltas are ignored by the integration.
- Per-turn buffering means the final answer is surfaced at the end of its
  generating turn rather than as each token arrives. This is a deliberate
  correctness trade-off: without it, intermediate narration from a tool-calling
  turn contaminates `answer_delta` and the reconstructed answer. A future
  milestone could revisit this if the model can be constrained not to narrate on
  tool turns, or if a retraction/reset protocol is added.
- Client disconnect terminates the stream naturally: `asyncio.CancelledError`
  is a `BaseException` and is deliberately not caught as an application error.
- Deferred to later milestones: the React UI, authentication, persistence,
  conversation memory, multiple agents, observability/tracing, MCP, deployment,
  and frontend state management. No streaming/SSE work exists in `agent.py`,
  `model.py`, or `tools.py`.
