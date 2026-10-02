# ADR-005: DeepSeek model integration via the Responses API

## Status

Accepted

## Context

The LangGraph boundary from ADR-004 is provider-neutral: `agent.py` compiles a
tool-calling loop from a caller-supplied `BaseChatModel` and knows nothing about
any provider. The next milestone must supply a real model without leaking
provider details into the graph.

The project fixed the model decision as DeepSeek `deepseek-flash` (DeepSeek V4.1
Flash), using DeepSeek's **Responses API** with reasoning effort `high`, at base
URL `https://api.deepseek.com`. The API key must not live in code or logs.

Two constraints shaped the design:

1. **The Responses API is required, not Chat Completions.** It is the interface
   DeepSeek documents for agent tooling, and preserving its structured response
   items leaves room for a later progress/streaming milestone.
2. **The provider must stay at the edge.** Only the model construction may know
   DeepSeek specifics; `agent.py` and the database tools stay unchanged.

The integration choice was verified against the installed environment
(`langchain-openai 1.6.7`, `openai 3.24.0`, `langchain-core 1.6.6`) and current
official DeepSeek and LangChain documentation.

## Decision

- **A small provider module, `backend/src/ai_sql_analyst/model.py`, owns all
  DeepSeek knowledge.** Its single responsibility is
  `environment/configuration -> DeepSeek ChatOpenAI model`, exposed as
  `build_model()`. It holds the constant model ID, base URL, reasoning effort,
  and API-key variable name.
- **`langchain-openai`'s `ChatOpenAI` is the integration.** It is the smallest
  supported LangChain chat model that can speak the Responses API.
- **The Responses API is selected explicitly:** `use_responses_api=True` and
  `output_version="responses/v1"`. The latter is retained deliberately so
  structured Responses items (reasoning, function calls, output text) remain
  available for the future streaming/progress milestone.
- **The model is `deepseek-flash` at `base_url="https://api.deepseek.com"`, with
  `reasoning={"effort": "high"}`.** DeepSeek's Responses API accepts
  `reasoning.effort` values `none|low|high|max`, and `high` is passed through
  verbatim by LangChain.
- **`DEEPSEEK_API_KEY` is the API-key environment variable.** `build_model()`
  reads it at call time (never import time, so importing the module and running
  the test suite need no secret), following the repo-root `.env` convention used
  by `db.py`. A missing key raises a `RuntimeError` naming the variable without
  echoing any secret material. The key is never hard-coded, logged, printed, or
  committed.
- **`agent.py` remains provider-neutral.** It still receives a `BaseChatModel`
  through `build_agent(model)`; no provider construction was added to it.
- **DeepSeek is treated as stateless.** `store` is left unset and
  `use_previous_response_id` is not used: DeepSeek's Responses API does not
  support server-side response chaining, and leaving `store` unset lets
  LangChain replay the reasoning items that DeepSeek requires across the tool
  loop. Setting `store=False` would drop that history.
- **Only one direct dependency is added:** `langchain-openai>=1.6.7`
  (transitively `openai`, `tiktoken`, `jiter`, `regex`). No provider package,
  no direct `openai` dependency, no provider abstraction.

## Alternatives considered

- **`langchain-deepseek` / `ChatDeepSeek`.** Rejected: despite LangChain's
  guidance to prefer a provider-specific package for DeepSeek, `ChatDeepSeek`
  cannot construct a Responses API payload (it reads a `messages` key that does
  not exist in the Responses payload) and raises before any HTTP request. It
  also depends on `langchain-openai` anyway, so it adds surface without
  enabling the required API.
- **Chat Completions (the legacy path).** Rejected: the project explicitly uses
  the Responses API, and Chat Completions would rely on provider-specific
  `reasoning_content` handling that this project does not want to build on.
- **The raw `openai` SDK.** Rejected: it would require writing a custom
  `BaseChatModel`, contradicting the "smallest supported dependency" principle.
- **`use_previous_response_id=True`.** Rejected: DeepSeek's Responses API is
  stateless and does not support `previous_response_id`; the generic LangChain
  troubleshooting advice for non-OpenAI backends does not apply here.
- **`store=False`.** Rejected as the default: it makes LangChain drop reasoning
  history on replay, which DeepSeek requires for the reasoning + tool loop.
- **A generic provider abstraction (ABC, registry, factory, routing,
  fallback).** Rejected: there is one provider; such machinery would be
  speculative. It can be introduced when a second provider actually exists.

## Consequences

- The database core, `tools.py`, and `agent.py` are untouched; the graph remains
  provider-neutral and independently testable with fake models.
- The project gains one direct dependency and one required environment variable
  (`DEEPSEEK_API_KEY`), documented in `.env.example` with an empty placeholder.
- The reasoning + tool loop works because reasoning items are replayed; this is
  protected by a deterministic test asserting the request representation and by
  an opt-in live smoke test that exercises a real `deepseek-flash` tool call.
- **Future streaming/progress milestone (deferred).** `output_version=
  "responses/v1"` preserves the structured Responses items needed for live
  agent progress and streamed output. Streaming, SSE, callbacks, FastAPI, and
  frontend work are explicitly out of scope here. Any future progress UI must
  derive concise status from agent/tool events, not surface raw chain-of-thought
  reasoning text.
- The integration is coupled to `langchain-openai`'s request construction. The
  deterministic wire-contract tests use private methods and pin the pinned
  version; if the provider or the integration drifts, those tests and the live
  smoke test are the first signal.
