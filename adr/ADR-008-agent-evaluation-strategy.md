# ADR-008: Agent evaluation strategy

## Status

Accepted

## Context

The application is feature-complete enough to evaluate: a provider-neutral
LangGraph agent (ADR-004) calls DeepSeek `deepseek-flash` (ADR-005), inspects
the live schema (ADR-001), and answers natural-language analytical questions by
proposing untrusted SQL that the read-only execution boundary (ADR-002) runs.

The open question is **how reliable the real agent is on representative Olist
questions**. That requires judging two very different things:

1. **Analytical correctness** — whether the agent's observations and final
   answer match what the data actually says. For the Olist dataset this is where
   the semantic traps live: `customer_unique_id` vs per-order `customer_id`,
   `order_items`/`order_payments` fan-out, non-unique `review_id`, incomplete
   category translations, naive timestamps, and valid `payment_installments = 0`
   / `not_defined` values.
2. **Agent behavior** — tool selection, schema inspection, recovery from SQL
   errors, scope/truncation handling, safety, and evidence discipline. Some of
   this is objective; some (ambiguity handling, unsupported causal claims) is
   genuinely subjective.

Two forces constrain the solution. First, correctness here is **empirical**:
the same trusted SQL, run against the live PostgreSQL database, yields the
ground truth, so no model should be asked to decide whether a number is right.
Second, evaluation must not distort the project: it is a portfolio-scale
application, not a benchmarking platform, and the existing architecture
(`agent.py`, `query.py`, `tools.py`, `api.py`) must stay untouched. Normal
`uv run pytest`/CI must remain free of a live database, a DeepSeek key, and a
heavy evaluation dependency.

## Decision

- **A versioned golden question set is the unit of evaluation.** Roughly 18–20
  curated Olist questions live in `backend/evals/dataset.jsonl`, each tagged
  with a category and a simple, inspectable check specification. Coverage spans
  simple aggregation, filtering, dates/grouping, customer identity, multi-table
  joins, payment/item fan-out, translation incompleteness, SQL error recovery,
  ambiguity, evidence discipline, truncation, timeout behavior, and write/DDL
  safety.
- **Deterministic PostgreSQL oracle checks are the authority for analytical
  correctness.** For numeric/categorical cases the evaluator executes a trusted
  oracle SQL query against the live database **through the existing
  `run_sql()` boundary**, compares it with the successful results captured from
  the agent's own `run_sql` calls, and additionally verifies that the final
  answer reports the same value. Comparison uses deterministic
  normalization/tolerance logic. **No LLM judge ever decides whether an exact
  number or categorical set is correct.**
- **DeepEval provides generic evaluation/tracing infrastructure, used narrowly.**
  Its native LangGraph `CallbackHandler`
  (`deepeval.integrations.langchain`) captures the agent run — model and tool
  spans, tool arguments, and tool outputs — so the runner does not hand-roll a
  trace format. DeepEval also supplies exactly one GEval-style qualitative
  metric for genuinely subjective cases (ambiguity handling, evidence
  discipline). `ToolCorrectnessMetric` is deliberately **not** used: OlistIQ
  exposes only two tools (`get_schema`, `run_sql`), so expected-tool behavior is
  objectively knowable and is checked deterministically without another LLM
  call. No other metrics, no generic evaluation abstraction, and no aggregate
  "agent score". DeepEval scores are reported separately and are never ground
  truth.
- **Evaluation code lives outside production modules.** A self-contained
  `backend/evals/` package (`dataset.jsonl`, `metrics.py`, `judge.py`,
  `run_evals.py`, `README.md`) drives the real agent and database. `metrics.py`
  holds the deterministic Olist checks and has no DeepEval dependency;
  `judge.py` is the only module that imports DeepEval metrics; the native
  `CallbackHandler` is attached only from `run_evals.py`. Nothing is added to
  `agent.py`, the graph nodes, or any production module to enable evaluation.
- **Capture-first, one real-agent run per case.** The runner builds the real
  agent once and invokes it once per case, attaching DeepEval's LangGraph
  `CallbackHandler` for that single run. The captured tool calls and results
  feed every deterministic check and the optional judge, so all metrics evaluate
  the same behavior and the model is never re-run per metric.
- **Live evaluation is explicitly opt-in and reports honestly.** It requires a
  live PostgreSQL database and `DEEPSEEK_API_KEY`. The runner writes a
  gitignored JSON report (run metadata, per-case trace, deterministic check
  breakdown, failure classification, optional DeepEval scores) and exits
  non-zero when a required deterministic check fails. A qualitative judge below
  its threshold is reported separately and does not by itself fail the run. No
  aggregate "agent score" is produced.
- **Normal CI remains free of DeepEval and of live model/database
  requirements.** DeepEval is an isolated optional dependency group
  (`evals`), not part of `dev`; `uv sync --locked --dev` and `uv run pytest`
  install and run exactly as before. Nothing in `agent.py`, `query.py`,
  `tools.py`, `api.py`, streaming, MCP, or the frontend is modified to
  accommodate evaluation.

## Alternatives considered

- **Manual evaluation only.** Rejected: it is exactly the current state, it does
  not scale, it is not reproducible, and it cannot demonstrate correctness
  against known Olist semantic traps. A versioned golden set plus an oracle is
  small to build and gives repeatable, evidence-backed signal.
- **LLM-as-judge for exact numerical/categorical correctness.** Rejected: a
  language model must not adjudicate a count, a rate, or a category set when a
  trusted SQL oracle against the same database answers the question exactly.
  Using a judge here would add cost, nondeterminism, and a plausible-but-wrong
  failure mode. Judges are reserved for genuinely subjective qualities.
- **A hosted evaluation platform such as LangSmith or Langfuse.** Rejected:
  the evaluation is a local, database-grounded correctness exercise; a hosted
  platform would add accounts, API keys, network dependence, and vendor lock-in
  without improving the deterministic oracle. DeepEval is used as a plain
  library, and its local mode only; no Confident AI account or telemetry is
  required.
- **Building a custom evaluation framework.** Rejected: an in-house metric
  engine, scoring DSL, or runner framework would be speculative. The project
  needs a golden set, an oracle, and a thin reporting runner — not a framework.
  The existing trusted SQL boundary (`run_sql`) and the existing agent
  (`build_agent`) are reused rather than re-implemented.
- **Decorating production nodes or the graph with `@observe` / custom
  instrumentation.** Rejected: it would couple evaluation to production code to
  support a richer trace than the milestone needs. The native LangGraph
  `CallbackHandler` is attached only from the runner, so the production graph
  stays untouched while the framework still captures the run.
- **A custom trace format / manual pairing of agent tool calls with tool
  results.** Rejected: it duplicated framework trace concepts and was the bulk
  of an over-large evaluation harness. DeepEval's native callback capture and
  the project-specific checks replace it, and the `rows` oracle comparison is
  positional rather than the permissive "any cell appears somewhere" check.
- **A second, custom provider path for the judge.** Rejected: the judge is a
  small `DeepEvalBaseLLM` adapter over the existing DeepSeek provider, so it
  speaks the same Responses API as the agent and provider knowledge stays in
  `model.py`.
- **An aggregate "agent score".** Rejected: a single number would hide which
  property failed and would mix deterministic correctness with subjective
  quality. Results are reported per case, per check, with an explicit failure
  classification.
- **Putting DeepEval in the `dev` group.** Rejected: it is heavy (transitive
  gRPC, OpenTelemetry SDK, telemetry, its own pytest plugins) and is only needed
  for opt-in live evaluation. A separate optional group keeps CI and normal
  development unchanged.
- **Fuzzing/synthetic benchmark generation or multi-run statistical analysis.**
  Rejected for this milestone: curated, human-reviewed Olist questions with
  oracle checks give higher-signal coverage than automated generation, and
  flakiness statistics can be added once a baseline exists.
- **Adding live evaluation to CI.** Rejected: CI has no database, no DeepSeek
  key, and should stay fast and deterministic. The live suite is run explicitly
  by a developer.

## Consequences

- The project gains an opt-in `backend/evals/` package and one optional
  dependency group (`evals`); production dependencies, CI, and normal test runs
  are unaffected.
- Analytical correctness is reproducible and evidence-backed: the same trusted
  SQL runs against the same database, and the agent's captured results and final
  answer are compared against it deterministically. The `rows` comparison is
  positional and order-preserving (a consistent contiguous column block), not a
  permissive "any cell appears somewhere" match.
- Tool-use expectations are deterministic (expected tools called, no unexpected
  tools, schema inspection when required); only answer quality/grounding uses a
  judge.
- Subjective qualities are evaluated narrowly and reported separately, so a
  qualitative judge can never masquerade as numerical truth.
- The evaluation reuses the real agent and the real `run_sql` boundary, so it
  tests production behavior rather than a parallel implementation. It does not
  weaken or bypass the read-only safety envelope.
- Because the suite is live and opt-in, it is not a regression gate; the first
  run is a **baseline**, not a hardening exercise. Failures are classified and
  recurring patterns are addressed in a later, evidence-backed hardening
  milestone rather than by changing prompts or agent logic immediately.
- DeepEval evicts a finished trace from its manager outside an evaluation
  session, so the runner reads the captured trace from the `CallbackHandler`
  object. This is evaluation-only code pinned to the locked DeepEval version; if
  that surface changes, only `run_evals.py` needs updating.
- Raw model reasoning is never captured or reported: the runner reads only the
  final answer text and tool calls, consistent with the privacy boundary in
  ADR-006.
