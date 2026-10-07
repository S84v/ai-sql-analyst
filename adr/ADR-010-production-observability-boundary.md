# ADR-010: Production observability boundary

## Status

Accepted

## Context

The agent is feature-complete and hardened: the provider-neutral LangGraph loop
(ADR-004) calls DeepSeek `deepseek-flash` (ADR-005), streams over SSE (ADR-006),
terminates after a SQL-timeout budget (ADR-009), and is bounded in production by
an explicit `recursion_limit` at the HTTP boundary. ADR-006 explicitly deferred
observability/tracing to a later milestone, and until now the only production
instrumentation was Python's standard `logging` (a single `logger.exception` in
`api.py`). The opt-in evaluation suite (ADR-008) has its own DeepEval tracing,
but that is evaluation-only and never runs in production.

The operational gap: a single `/query` request is not traceable end to end. We
cannot see, for one request, the HTTP boundary, the LangGraph run, the model
turns, the tool calls, the SQL executions, and the final outcome as one
connected picture. The requirement is proportional: this is a portfolio-scale
application, not an observability platform.

Two constraints shape the decision. First, the existing boundaries are fixed:
`agent.py` is provider-neutral, `model.py` is the only provider integration,
`query.py` is the SQL trust boundary, `tools.py` is a thin adapter,
`streaming.py` is the event translator, and `api.py` is the HTTP boundary.
Second, the application handles user questions, generated SQL, and database
rows; telemetry must not become a new data-exfiltration surface.

## Decision

- **OpenTelemetry is the instrumentation standard.** Not LangSmith, Langfuse, or
  another hosted platform. Telemetry is emitted with the vendor-neutral OTel API
  and SDK, and export is opt-in to a standard OTLP/HTTP endpoint. No hosted
  backend, no vendor account, and no collector are required.
- **Official framework instrumentation is used where it exists.** FastAPI server
  spans come from `opentelemetry-instrumentation-fastapi`; LangGraph/LangChain
  workflow, agent, model-inference, and tool spans come from the OpenTelemetry
  project's `opentelemetry-instrumentation-genai-langchain`. No custom
  LangGraph callback/tracing system is built.
- **A single narrow module owns production instrumentation.**
  `backend/src/ai_sql_analyst/observability.py` owns provider setup, tracer/meter
  creation, FastAPI and LangChain/LangGraph instrumentation, opt-in export, the
  request/run outcome helper, and provider shutdown. It is deliberately not a
  configuration framework.
- **The SQL execution boundary is instrumented manually.** `query.py`—the SQL
  trust boundary—emits one `run_sql` span and two application metrics
  (`olistiq.sql.executions`, `olistiq.sql.duration`). `query.py` uses only the
  OpenTelemetry **API**; it imports no LangChain, LangGraph, or FastAPI, so it
  stays logically framework-neutral and independently testable, and the MCP edge
  (ADR-007), which shares `query.py`, is affected only as a no-op unless a
  provider is configured.
- **The HTTP server span carries the run outcome.** `api.py` records
  `olistiq.run.outcome` on the current server span, one of `success`,
  `no_answer`, `timeout_stop`, `recursion_limit`, or `error`. The timeout-stop
  value is derived from the existing terminal node (`TIMEOUT_STOP_NODE`,
  ADR-009) observed on the raw event stream; recursion-limit exhaustion is
  classified by catching `GraphRecursionError`. These are classification-only:
  the SSE protocol and the emitted client events are unchanged.
- **Export is opt-in and off by default.** No exporter is configured unless
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP/HTTP) or `OLISTIQ_OTEL_CONSOLE=1`
  is set (local console export to **stderr**). With neither set, no providers are
  created and the LangChain instrumentor is not installed, so the default path
  is a no-op. Console export writes to stderr because the MCP stdio transport
  owns stdout.
- **Setup is idempotent.** OTel providers and the LangChain instrumentor are
  process-global; `observability.py` guards provider creation and instrumentation
  with module state, and guards FastAPI instrumentation per app, so the
  module-level default app, tests, and multiple apps coexist safely.
- **The `langchain` meta-package is not added.** The GenAI instrumentor's
  distribution check targets `langchain`, but it only imports and wraps
  `langchain_core` and `langgraph`, which the project installs. Instrumentation
  is enabled with `instrument(skip_dep_check=True)` rather than pulling the full
  meta-package.

### Privacy / content-capture policy

- **Metadata/structure only.** Questions, prompts, the system prompt, model
  reasoning, raw SQL, SQL parameters, result rows, column values, final answer
  text, and customer/order/seller identifiers are never captured or logged by
  telemetry. Database credentials, DSNs, and API keys are never recorded.
- **Content capture is disabled explicitly.** `observability.py` pins
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` before
  instrumenting, which disables prompt, completion, and tool-call
  argument/result capture (the upstream default, made explicit here).
- **No sanitizing exporter framework.** Privacy is enforced by that explicit
  setting and by application code never recording sensitive values, not by a
  custom attribute-stripping span processor.
- **`record_exception()` is not used.** For failures the SQL span records only
  the coarse `error.kind` classification and sets span status; raw database
  error messages are kept out of spans. Unexpected exceptions remain generic to
  the client and detailed only through normal server-side logging.
- **Low-cardinality dimensions only:** `outcome`, `tool_name` (from official
  spans), `db.system`, `db.operation`, `olistiq.sql.truncated`, HTTP method and
  status. No question text, SQL text, or identifiers as span attributes or
  metric labels.

### Boundary ownership

`observability.py` observes; it never redefines behavior. `agent.py` stays
provider-neutral, `model.py` stays the only provider integration, `query.py`
stays the SQL trust boundary (its validation, timeout, error classification, row
limit, and result contract are unchanged), `tools.py` stays a thin adapter,
`streaming.py` stays the application event translator and SSE protocol owner,
and `api.py` stays the HTTP boundary. The timeout budget, the recursion limit,
the graph topology, `MessagesState`, and the system prompt are untouched.

## Alternatives considered

- **A hosted platform (LangSmith / Langfuse).** Rejected: it would add a hosted
  dependency and account for a portfolio-scale application, and the project
  already deliberately keeps DeepEval out of production and telemetry opted out.
- **LangSmith's built-in OTel export (`langsmith[otel]`).** Rejected: it is
  hosted-by-default and couples observability to the LangSmith SDK; the OpenTelemetry
  project's own instrumentation plus a standard OTLP/HTTP exporter is more
  neutral and needs no vendor.
- **OpenInference / Traceloop instrumentation.** Rejected as the primary choice:
  both are capable and mature, but they are third-party instrumentations; the
  official OpenTelemetry GenAI instrumentation was preferred. The beta status of
  the official package is accepted and documented below.
- **Adding the full `langchain` meta-package to satisfy the instrumentor.** Rejected:
  the instrumentor needs only `langchain_core`/`langgraph`; `skip_dep_check=True`
  avoids a large, unnecessary dependency.
- **A custom LangGraph callback/span system.** Rejected: the official
  instrumentation already provides workflow/agent/model/tool spans; hand-rolling
  would duplicate framework concepts and couple production code to telemetry.
- **Instrumenting `psycopg` directly instead of `run_sql`.** Rejected: it would
  not know the run_sql-specific outcome (validation/timeout/truncation) and would
  add another instrumentation for no extra signal.
- **A custom content-stripping `SpanProcessor`.** Rejected: the explicit
  `NO_CONTENT` setting plus not recording sensitive values is sufficient; a
  sanitizing/export-rewrite layer would be a framework in disguise.
- **A collector / Prometheus / Grafana / Jaeger stack.** Rejected for this
  milestone: opt-in OTLP/HTTP or stderr console export is enough to make a
  request traceable, and verification uses in-process exporters.
- **`structlog` or a new logging framework.** Rejected: standard `logging`
  remains sufficient.

## Consequences

- The project gains five runtime dependencies (`opentelemetry-api`,
  `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`,
  `opentelemetry-instrumentation-genai-langchain`,
  `opentelemetry-exporter-otlp-proto-http`) and their transitive packages. No
  `dev` dependency is added.
- The default production path is a no-op with respect to export; instrumentation
  is installed only when an exporter is configured. Enabling OTLP/HTTP or console
  export makes one request traceable from the HTTP boundary through the graph,
  model, and tools to the SQL boundary, plus SQL metrics.
- The official GenAI LangChain instrumentation is currently a beta release
  (`opentelemetry-instrumentation-genai-langchain` 1.x-b); it wraps
  `langchain_core`/`langgraph` and requires `skip_dep_check=True` in this
  dependency set. Its span/attribute surface may change across releases.
- **Context-propagation caveat:** the HTTP → workflow → model/tool nesting is
  expected from the official instrumentors, and OTel context survives the
  LangGraph sync-node thread hop because `langchain_core` copies the context into
  `run_in_executor`. Whether the manual `run_sql` span nests exactly under the
  `execute_tool run_sql` span (versus attaching to the workflow span) must be
  verified live; no custom context-propagation machinery is added for it.
- The evaluation suite, MCP edge, frontend, timeout behavior, recursion limit,
  graph semantics/state, and system prompt are unchanged. MCP never configures
  observability, so the stdio protocol is unaffected.
