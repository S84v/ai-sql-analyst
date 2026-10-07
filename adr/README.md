# Architecture Decision Records

ADRs document significant architecture and design decisions for AI SQL Analyst:
what was decided, why, and what alternatives were rejected. They capture
reasoning that would otherwise be lost, not routine implementation details.

## Index

- [ADR-001](ADR-001-dynamic-schema-introspection.md) — Dynamic, catalog-based schema introspection
- [ADR-002](ADR-002-read-only-sql-execution-boundary.md) — Read-only SQL execution boundary
- [ADR-003](ADR-003-langchain-tool-adapter.md) — LangChain tool-adapter layer
- [ADR-004](ADR-004-langgraph-agent-boundary.md) — LangGraph agent boundary
- [ADR-005](ADR-005-deepseek-responses-model-integration.md) — DeepSeek model integration via the Responses API
- [ADR-006](ADR-006-http-streaming-boundary.md) — Streaming HTTP boundary
- [ADR-007](ADR-007-mcp-tool-adapter.md) — MCP tool-adapter layer
- [ADR-008](ADR-008-agent-evaluation-strategy.md) — Agent evaluation strategy
- [ADR-009](ADR-009-timeout-budget-termination.md) — Timeout-aware termination in the agent loop
- [ADR-010](ADR-010-production-observability-boundary.md) — Production observability boundary

## Conventions

- ADRs are numbered sequentially: `ADR-001`, `ADR-002`, ... Numbers are never
  reused.
- Filenames use a short kebab-case description after the number, e.g.
  `ADR-001-dynamic-schema-introspection.md`.
- Each ADR contains these sections:
  - **Status** — e.g. Proposed, Accepted, Superseded.
  - **Context** — the forces and constraints that prompted the decision.
  - **Decision** — what was chosen.
  - **Alternatives considered** — what was rejected and why.
  - **Consequences** — resulting trade-offs, benefits, and follow-ups.
- Create an ADR when a significant architectural decision is *finalized*, not
  for routine implementation details.
- When a decision is replaced, update the old ADR's **Status** to
  `Superseded by ADR-XXX` and note the replacement; do not delete it.

`AGENTS.md` carries the workflow rule; this directory carries the decisions.
