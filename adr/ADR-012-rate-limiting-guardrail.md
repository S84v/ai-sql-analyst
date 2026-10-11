# ADR-012: Best-effort, process-local rate limiting for `POST /query`

## Status

Accepted

## Context

ADR-011 deployed OlistIQ publicly and recorded that "the API remains
unauthenticated and rate-limit-free for now; authentication and rate limiting
are future hardening items." The public `POST /query` endpoint runs an
LLM-backed agent and read-only SQL, so an unbounded caller can impose real
DeepSeek and database cost. A full security architecture (authentication,
gateway quotas, distributed counters) is out of proportion to a sporadic,
low-traffic portfolio application. What is needed is a small, coherent guardrail
that blunts casual abuse without new infrastructure.

The request path is the browser calling Cloud Run directly over HTTPS/SSE (there
is no Hosting proxy for `/query`), so Cloud Run is the only ingress. A temporary
diagnostic confirmed what the application receives there; that observation is the
basis of the client-address assumption below.

## Decision

Add a **fixed-window rate limiter** applied only to `POST /query`, before the
agent is invoked or the SSE response starts.

- **Policy:** The first request opens the window and counts as request one. Up
  to 10 requests total are allowed per client key in each 60-second window;
  subsequent requests are rejected with HTTP **429** and a `Retry-After` header
  (whole seconds until the window resets).
- **Implementation:** a standard-library-only, in-memory counter
  (`ai_sql_analyst/ratelimit.py`) using `time.monotonic()` (injectable clock),
  guarded by a `threading.Lock`. Expired entries are swept during checks, with no
  background task. No dependencies, external services, or configuration flags.
- **Enforcement point:** a FastAPI route dependency on `POST /query` only, so a
  rejected request never reaches the endpoint body and never invokes the agent,
  model, or SQL tools. CORS preflight (`OPTIONS /query`) is handled by the CORS
  middleware and is not rate-limited.
- **Client key — best-effort, forwarded-IP assumption:** the key is the
  **rightmost** entry of the received `X-Forwarded-For` chain, validated with
  `ipaddress`. Earlier entries are treated as attacker-influenced. If the
  rightmost entry is missing or malformed, a single shared `unknown` key is used
  — the limiter never falls back to the internal `request.client.host` and never
  fails open. This is a documented assumption about the observed Cloud Run
  request, not a guarantee of spoof-proof identity: it discourages casual abuse
  but does not by itself prevent deliberate IP manipulation.
- **Scope of state:** one limiter instance per application process.

## Alternatives considered

- **No rate limiting (status quo).** Rejected: it leaves unbounded cost exposure
  on a public LLM endpoint.
- **A distributed counter (Redis / Memorystore) or API gateway quota.**
  Rejected: always-on infrastructure and operational weight that ADR-011
  deliberately avoids for sporadic traffic.
- **A database-backed counter.** Rejected: couples request admission to the
  database and adds write load for a job unrelated to the analytical data.
- **Trusting the leftmost `X-Forwarded-For` entry.** Rejected: it is
  client-controllable and trivially spoofed.
- **Falling back to `request.client.host`.** Rejected: with the current Uvicorn
  configuration it is the proxy-internal peer address, so it would collapse all
  callers into a single bucket and misrepresent the client.
- **Trusting `request.client.host` via broad proxy-header changes
  (`--forwarded-allow-ips=*`).** Rejected: it is a broad proxy-trust change, and
  it would make Uvicorn select the spoofable leftmost value.
- **Failing open when the client cannot be identified.** Rejected: it provides no
  protection against exactly the traffic that is hardest to attribute.
- **Authentication.** Out of scope for this decision; a stronger future measure.
- **A sliding-window or token-bucket limiter.** Rejected as more complexity than
  the policy requires.

## Consequences

Positive:

- Blunts casual/burst abuse of the expensive endpoint with no new infrastructure
  or dependencies.
- Rejected requests are cheap: they never invoke the agent, model, or SQL.
- The decision and its limits are explicit rather than implicit.

Costs, limits, and accepted risks:

- **Not a global quota.** State is per process, so the effective ceiling
  multiplies by the number of running instances (Cloud Run scales to a small
  maximum), and it resets on every cold start/restart. Under concurrent load the
  real limit can be higher than 10/60 s for a single client.
- **Best-effort client identity.** The rightmost-`X-Forwarded-For` assumption is
  not spoof-proof; a caller who can influence the last hop can still shift their
  apparent identity. This guards against casual abuse, not determined attackers.
- **Shared `unknown` bucket.** Callers whose address cannot be parsed share one
  budget, so unidentifiable traffic can be throttled together.
- **No persistence / no metrics.** Counters are in memory only; there is no
  cross-instance coordination and no dedicated rate-limit telemetry.
- **Uvicorn proxy-header trust is unchanged.** This decision did not modify the
  Dockerfile, Uvicorn settings, Cloud Run configuration, or workflows.

Follow-ups (not implemented here): authentication, coordinated/distributed
limiting, and rate-limit observability.

## Relationship to existing decisions

Additive. It preserves ADR-006 (the SSE HTTP boundary — limiting happens before
the stream starts), ADR-004 (the provider-neutral agent), and ADR-011 (it uses no
new infrastructure and narrows an explicitly deferred hardening item). It does
not change production configuration.
