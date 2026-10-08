# Agent evaluation

Opt-in, live evaluation of the real OlistIQ agent
(`React → FastAPI → LangGraph → DeepSeek → PostgreSQL`). It adds no production
code, does not run in CI, and is not a regression gate: the first run is a
**baseline** to classify failures, not a hardening step.

## How it is put together

```text
golden dataset (dataset.jsonl)
        ↓
run_evals.py  ── attaches DeepEval's LangGraph CallbackHandler
        ↓
real OlistIQ LangGraph agent  (build_agent(build_model()))
        ↓
PostgreSQL  (through the existing run_sql() boundary)
        ↓
deterministic Olist metrics (metrics.py)  +  one qualitative DeepEval judge (judge.py)
```

- `dataset.jsonl` — 20 curated golden cases. Each line is compact: `id`,
  `category`, `question`, optional `oracle_sql`/`comparison`/`tolerance`,
  optional `expected_tools`, `requires_schema`, behavioral `checks`, `notes`.
- `run_evals.py` — the single entry point. Builds the real agent once, invokes
  it once per case with DeepEval's native `CallbackHandler`, reads the captured
  tool calls from the framework trace, runs the deterministic metrics, optionally
  the qualitative judge, and writes a JSON report.
- `metrics.py` — deterministic, Olist-specific. No DeepEval dependency.
- `judge.py` — the only module importing DeepEval metrics; one GEval metric for
  the two subjective cases.
- `reports/` — generated reports (gitignored).

## Current baseline

The golden set has **20 cases**. The latest verified run passed **20/20
deterministic cases** (0 failed), recorded on **2026-10-08** against code revision
**`4a713ce`** with the qualitative LLM judge disabled. This is a baseline over a
small, curated, representative set — not a statistical accuracy claim.

The cases span simple aggregation, filtering, dates/grouping, customer identity,
multi-table joins, item/payment fanout, translation completeness, review grain,
SQL error recovery, ambiguity, evidence discipline, truncation, timeout behavior,
and write/DDL safety.

- **Deliberate timeout stress (`expensive_geolocation_join`):** this case runs an
  intentionally expensive computation (an O(n²) operation over the ~1,000,163-row
  geolocation table). The computation may still time out — the improvement is not
  that the query became cheap. ADR-009 introduced a fixed budget of three total
  SQL timeout failures; once the budget is exhausted the graph terminates through
  the `timeout_stop` node with a deterministic, non-fabricating answer instead of
  retrying until the recursion limit. The case passes by respecting the timeout
  boundary and not fabricating a result.
- **Exact correctness** is decided only by the deterministic PostgreSQL oracle
  checks. The single limited GEval is qualitative and informational; it does not
  drive pass/fail.
- The baseline is tied to the current code, model, and dataset versions; re-run
  the suite after changing any of them.

Deterministic evaluator regression tests live in
`backend/tests/test_eval_metrics.py` (**7 tests**, no DeepSeek/PostgreSQL) and run
with the normal backend suite. The live database and model tests remain opt-in
(gated behind environment flags, so they skip by default).

Evaluation is **opt-in and outside normal CI**: `uv sync --group evals` plus a
live database and `DEEPSEEK_API_KEY` are required, and normal
`uv sync --locked --dev` never installs DeepEval.

## Why DeepEval

DeepEval provides the generic evaluation machinery so the runner does not hand-roll
a trace format: its native LangGraph `CallbackHandler`
(`deepeval.integrations.langchain`) captures the agent's model/tool spans and tool
calls during the run. The project keeps only the checks that need Olist knowledge
and one qualitative judge.

**`ToolCorrectnessMetric` is intentionally not used.** OlistIQ exposes exactly two
tools (`get_schema`, `run_sql`), so expected-tool behavior is objectively knowable
and does not justify another LLM call. It is checked deterministically in
`metrics.py`: `requires_schema=true` ⇒ `get_schema` called; `expected_tools`
present ⇒ each was called; no unexpected tool names.

## Deterministic vs qualitative

Deterministic (the authority, drives the exit code):

- **Oracle correctness** — a trusted `oracle_sql` runs through the existing
  `run_sql()` and is compared with the agent's observed `run_sql` results.
  `rows` comparison is positional and order-preserving (the oracle's columns must
  appear as one consistent contiguous block), `set` compares the first column,
  `scalar` matches within tolerance. The final answer must also report the oracle
  value(s). No LLM judges a number.
- Olist grain semantics (customer identity, item/payment fanout, translation
  completeness, review grain), safety invariants, truncation/scope, and
  timeout/fabrication behavior.

Qualitative (DeepEval, informational, never ground truth, not exit-driving):

- One `GEval` metric ("Answer grounding and scope") for `best_customers` and
  `delivery_worsened_why` only — ambiguity handling, evidence discipline,
  unsupported causal claims, scope preservation, stating assumptions.

No aggregate agent score is produced.

## Judge and the DeepSeek Responses API

The judge is a small custom `DeepEvalBaseLLM` adapter that wraps
`ai_sql_analyst.model.build_model()`, so it speaks the **same DeepSeek Responses
API** as the production agent. Provider constants are not duplicated and no
provider code moves into the graph.

## Install and run

DeepEval lives in an optional dependency group, so normal development and CI
(`uv sync --locked --dev`) never install it:

```bash
cd backend
uv sync --group evals
uv run python evals/run_evals.py                 # full live evaluation
uv run python evals/run_evals.py --no-judge      # deterministic checks only
uv run python evals/run_evals.py --case unique_buyers --case repeat_customers
uv run python evals/run_evals.py --validate      # dataset only; no DB/model
```

- Required: a reachable PostgreSQL database
  (`POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`, optional host/port) and
  `DEEPSEEK_API_KEY`, read from the repo-root `.env`.
- Local only: DeepEval telemetry is disabled
  (`DEEPEVAL_TELEMETRY_OPT_OUT=1`); no Confident AI account or API key is used.
- Exit code `0` only when all required deterministic checks pass; `1` otherwise.
  A qualitative judge below threshold is reported separately and does not fail
  the run.
- Reports are written to `reports/run-<UTC timestamp>.json` with run metadata
  (timestamp, git HEAD, dataset hash), per-case results, the oracle, the observed
  SQL/tool trace, tool-error count, truncation/recovery signals, the final
  answer, check breakdown, failure class, and the judge score/reason when used.

## Compatibility note

DeepEval evicts a finished trace from its manager when not inside an evaluation
session, so the runner reads the captured trace from the `CallbackHandler`
object. This is evaluation-only code pinned to the locked DeepEval version; if
DeepEval changes that surface, only `run_evals.py` needs updating.

## Privacy

Only the final answer text and tool calls are captured. Raw model reasoning /
chain-of-thought is never read or stored (ADR-006).
