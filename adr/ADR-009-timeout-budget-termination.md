# ADR-009: Timeout-aware termination in the agent loop

## Status

Accepted

## Context

ADR-004 fixed the agent loop as `START -> agent -> tools -> agent ...` and relied
solely on LangGraph's execution limit (`recursion_limit`) for termination,
deliberately avoiding a custom iteration or recursion counter. Live evaluation
(ADR-008) exposed the consequence for one class of question. The canonical case
`expensive_geolocation_join` ("average straight-line distance between all pairs
of geolocation points that share a ZIP prefix", an O(n^2) computation over
~1,000,163 rows) repeatedly exhausts the recursion limit at 25 and ends with
`failure_class=timeout_recovery`.

The captured SQL trace shows the model is not simply repeating one statement. It
intersperses genuine diagnostics (schema inspection, cheaper checks, narrower
formulations) with repeated expensive formulations that time out. `run_sql`
already returns a structured `{ok: false, error: {kind: "timeout"}}` result, and
`ToolNode` persists every tool interaction as a `ToolMessage`. What was missing
was a *semantic* stopping rule: the loop had no way to stop after repeated
evidence that the requested computation is impractical within the execution
boundary.

Increasing `recursion_limit` does not add a stopping rule; it only permits more
timeout cycles, more database load, higher latency, and still ends in
`timeout_recovery`. Counting generic tool iterations is too blunt: the same trace
shows legitimate multi-query exploration that must not be aborted. Counting only
*consecutive* timeouts is defeated by the successful diagnostic query that sits
between timeouts in the trace -- exactly the behavior that allowed the loop to
continue indefinitely.

## Decision

- **Derive the timeout count from existing state, not a new field.** The number
  of `run_sql` timeout failures is read from the persisted `ToolMessage` history
  (`name == "run_sql"` with parsed `error.kind == "timeout"`). State remains
  `MessagesState` only: no custom state field, no reducer, no `ToolNode` wrapper,
  and no duplicated counter to keep synchronized.
- **Use a fixed budget of three total timeouts per request** (`TIMEOUT_BUDGET`).
  The budget counts the total number of timeout failures, not a streak; a
  successful diagnostic query does not reset it.
- **Allow recovery below the budget.** Turns with fewer than three timeouts keep
  routing to the model, so cheap diagnostics and semantics-preserving
  reformulations still work.
- **Terminate deterministically at the budget.** The static `tools -> agent` edge
  becomes conditional: below budget it routes to `agent`; at or above budget it
  routes to a `timeout_stop` node. That node emits a fixed `AIMessage`
  (`TIMEOUT_STOP_MESSAGE`) and the graph ends. The application never asks the
  model for another turn once the budget is exhausted.
- **The terminal answer is grounded and non-fabricating.** It states that the
  computation exceeded the execution time limit on repeated attempts and that no
  estimate was produced; it introduces no numbers and does not sample, estimate,
  or redefine the user's requested calculation.
- **The system prompt gains one targeted timeout bullet.** A timeout means the
  computation may exceed the budget; a materially cheaper, semantics-preserving
  reformulation is allowed; retrying expensive variants indefinitely is not; a
  `LIMIT` does not make an upstream expensive join or pairwise computation
  cheaper; after repeated timeouts the model should stop rather than estimate or
  silently change the requested calculation.
- **The streamed boundary surfaces the terminal message.** The terminal answer is
  produced by a non-`agent` node and arrives as a graph update rather than a
  streamed chunk, so `streaming.translate_agent_events` maps the `timeout_stop`
  update to the existing `answer_delta` + `done` protocol instead of the generic
  "no answer" error.

### Relationship to ADR-004

ADR-004 remains **Accepted and unchanged**. This ADR narrows two of its
statements for the specific, semantically identified case of SQL timeouts: the
claim that termination uses only LangGraph's execution limit with "no custom
iteration machinery", and the exact shape `tools -> agent`. The state model is
*not* changed -- the count is derived from the message history that ADR-004
already designates as the record of tool interactions, so `MessagesState` remains
the single source of state.

## Alternatives considered

- **Raise `recursion_limit`.** Rejected: preserves the failure, increases
  database load and latency, and stays non-deterministic; it treats the symptom.
- **Cap generic tool iterations.** Rejected: legitimate multi-query exploration
  (schema checks, iterative narrowing) would be cut off, harming correct
  questions. The cap is deliberately keyed to `error.kind == "timeout"`.
- **Count consecutive timeouts only.** Rejected: a successful diagnostic between
  timeouts resets the streak, which is precisely how the observed loop ran
  indefinitely.
- **A reducer-backed counter state field or a `ToolNode` wrapper.** Rejected:
  adds state schema, duplication, and synchronization burden that ADR-004 already
  declined ("treats messages as the record of tool interactions").
- **Prompt-only guidance.** Rejected: the live trace shows the model already
  explores and ignores prompt-level restraint under repeated failure; a
  deterministic application-level boundary is required.
- **Abort/raise on the first timeout.** Rejected: early timeouts are legitimately
  recoverable and must not become hard failures.

## Consequences

- A timeout-driven run now terminates in a bounded number of super-steps, well
  under the default recursion limit, with a deterministic final `AIMessage`.
- Existing consumers keep working: the DeepEval runner reads the last
  tool-call-free `AIMessage` (so `answer_present` is true and
  `recursion_limit_hit` is false), and the SSE translator emits a normal
  `answer_delta` + `done`. `expensive_geolocation_join` is expected to move from
  `timeout_recovery` to a deterministic pass; the live baseline should be
  re-confirmed.
- The count relies on `ToolNode` setting `ToolMessage.name` and JSON-serializing
  dict results. This is the same assumption `streaming._tool_result_status`
  already makes; it is framework-version-sensitive but not new coupling.
- ADR-004's "MessagesState only" and "no custom iteration machinery" statements
  are now qualified by this ADR. Per ADR conventions, ADR-004's text is left
  intact rather than rewritten.
