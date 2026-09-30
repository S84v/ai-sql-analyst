# Architecture Decision Records

ADRs document significant architecture and design decisions for AI SQL Analyst:
what was decided, why, and what alternatives were rejected. They capture
reasoning that would otherwise be lost, not routine implementation details.

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
