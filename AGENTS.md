# AGENTS.md

## Project purpose
AI SQL Analyst is a full-stack application for asking natural-language analytical
questions over PostgreSQL, answered by an LLM-powered agent.

## Architecture
- The React frontend communicates with FastAPI over HTTP.
- FastAPI invokes the LangGraph agent.
- LangGraph handles agent state and orchestration.
- LangChain handles model and tool integration.
- Controlled application tools interact with PostgreSQL.
- The LLM never receives unrestricted database credentials.
- The LLM proposes SQL; application code validates and executes it.

## Layout and toolchain
- `backend/` — Python 3.12+ service managed with `uv`, using a `src/` layout
  (`backend/src/ai_sql_analyst/`, distribution name `ai-sql-analyst`). Entry
  point is `ai_sql_analyst:main`. Run it from `backend/` with
  `uv run ai-sql-analyst`.
- `frontend/` — React + TypeScript + Vite SPA managed with `npm`.
- `data/raw/` — raw input datasets.

### Frontend commands (run from `frontend/`)
- `npm run dev` — Vite dev server.
- `npm run build` — `tsc -b && vite build`; this is the typecheck/build step.
- `npm run lint` — oxlint (not ESLint).
- `npm run preview` — serve the production build.

### Frontend TypeScript constraints
- `noUnusedLocals` / `noUnusedParameters` fail the build on unused symbols.
- `verbatimModuleSyntax` requires `import type` for type-only imports.
- `erasableSyntaxOnly` forbids TS-only runtime syntax (enums, parameter properties).

## Engineering principles
- Keep the architecture simple.
- Prefer small, composable modules.
- Avoid premature abstractions.
- Avoid unnecessary dependencies and infrastructure.
- Do not make unrelated changes during a task.
- Preserve the agreed architecture unless a change is explicitly discussed.
- Keep production concerns proportional to the current milestone.

## SQL / database safety
- Generated SQL is untrusted input.
- Database access must go through controlled application code.
- Write operations are not part of the intended agent capability.
- Strengthen safety mechanisms incrementally as milestones require them.

## OpenCode workflow
- For significant changes, inspect and plan before implementing.
- For small, obvious changes, implementation can proceed directly.
- Make small, focused changes.
- Run focused verification after changes.
- Summarize files changed and important design decisions.
- Do not silently introduce major architectural changes.
- Stop and surface significant architecture changes for review.

## Git and commits
- Use Conventional Commits for all commits.
- Format: `<type>: <description>`.
- Common types for this project:
  - `feat` — new functionality
  - `fix` — bug fixes
  - `refactor` — behavior-preserving restructuring
  - `test` — tests
  - `docs` — documentation
  - `chore` — maintenance/tooling
  - `build` — build/dependency changes
  - `ci` — CI/CD changes
  - `perf` — performance improvements
- Keep commit messages concise and specific.
- Make small logical commits rather than large milestone-spanning commits.
- Do not rewrite existing history merely to change commit message style.

## Code comments
- Explain why, security boundaries, non-obvious framework behavior, database
  lifecycle, and important design decisions.
- Avoid comments that merely restate the code.

## Architecture Decision Records
- Inspect relevant ADRs in `adr/` before proposing significant architecture
  changes.
- Significant finalized architecture decisions must be recorded in `adr/`.
- ADRs explain the rationale and the rejected alternatives, not just the choice.
- Trivial implementation details do not require an ADR.
- `AGENTS.md` holds the workflow rule; `adr/` holds the actual decisions.

## CI
- GitHub Actions runs backend pytest on pushes to main and pull requests.
- Keep local test commands consistent with the CI workflow.

## MCP usage

Use connected MCP servers when they materially improve the current task. Do not invoke MCP tools merely because they are available.

### Source-of-truth hierarchy

For this project, prefer evidence in this order:

1. Actual local repository files and local dataset observations.
2. Directly derived results from the local database or source data.
3. Official technical documentation.
4. External research and community sources.

External research must not override verified local project evidence without an explicit design discussion.

### Context7

Use Context7 when current or version-specific documentation is needed for a library, framework, API, or SDK.

Prefer official/current documentation over remembered API behavior.

### Tavily

Use Tavily when external web research is genuinely required, especially for:

* current documentation or behavior
* niche technical questions
* comparing implementation approaches
* investigating external project conventions

Do not use web research to replace profiling or validation of the local Olist dataset.

### GitHub

Use GitHub when repository history or external repository information is relevant, such as:

* inspecting project history
* checking upstream implementations
* reviewing issues or pull requests
* consulting external source repositories

Prefer the local working tree when the question concerns the current project state.

### Playwright

Use Playwright for browser-level verification of frontend behavior, including:

* UI interaction
* request/response behavior
* rendering
* browser console/network issues
* end-to-end tests

Do not use Playwright for backend-only or database-only tasks.

### Tool-use discipline

Before using an MCP server, determine whether it materially improves the task.

Do not:

* perform redundant external research
* replace local inspection with web searches
* use MCP results as authoritative when verified project evidence exists
* introduce architecture changes solely because an external example uses them
* use MCP tools for unrelated exploration

When external information conflicts with project evidence, surface the conflict explicitly and stop before making a significant architectural change.
