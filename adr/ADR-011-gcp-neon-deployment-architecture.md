# ADR-011: Production deployment architecture (GCP + Neon)

## Status

Accepted

## Context

The application is feature-complete and hardened: a provider-neutral LangGraph
agent (ADR-004) over DeepSeek `deepseek-flash` (ADR-005), streamed over SSE by a
thin FastAPI boundary (ADR-006), terminating on a SQL-timeout budget (ADR-009),
bounded by an explicit recursion limit, and instrumented with opt-in
OpenTelemetry (ADR-010). Until now it has run only locally: PostgreSQL 16 via
`docker-compose.yml`, FastAPI on `uvicorn`, and a Vite dev server that proxies
`/query` to `http://127.0.0.1:8000` (`frontend/vite.config.ts`). CI verifies the
backend and frontend, but nothing is deployed.

The project now needs a first production deployment. The constraints are:

- **The architecture is fixed and must not be redesigned.** The frontend is a
  static React/Vite SPA; the backend is a single FastAPI/LangGraph service; the
  database boundary (`query.py`/`db.py`) is framework-neutral and must stay so.
- **Traffic is sporadic portfolio/recruiter traffic** — mostly idle, with
  occasional short bursts — and there is no high-availability requirement.
- **The database is small** (the Olist schema; roughly 250 MB across nine
  standard relational tables) and has no provider-specific requirements.
- **The first deployment should be simple enough to operate manually and
  verify** before automation is added (AGENTS.md deployment rules).
- **Secrets must never be committed**, and database configuration should keep
  the existing `POSTGRES_*` concept where practical.

The question: what is the smallest production architecture that hosts the
existing application with minimal application/database redesign and no
always-on infrastructure?

## Decision

Production topology:

```
Browser -- HTTPS (static) ----------> Firebase Hosting ----> React/Vite SPA (static assets)

Browser -- HTTPS + SSE (direct) ----> Cloud Run (single service) --> FastAPI / LangGraph
                                                                 --> Neon PostgreSQL
                                                                 --> DeepSeek API
```

- **Firebase Hosting serves the static frontend.** Hosting is used only to
  deliver the built SPA assets and to own the public site origin.
- **Cloud Run runs the single backend service.** It is a natural container
  runtime for the existing ASGI app. It starts at the default scale-to-zero
  posture (`min-instances = 0`). There is one service: no Kubernetes, no
  microservices, no service mesh.
- **The browser calls the Cloud Run HTTPS origin directly in production.**
  `/query` remains the existing POST + SSE application boundary (ADR-006); the
  browser opens a direct HTTPS/SSE connection to Cloud Run. Consequences of the
  direct path:
  - the production frontend must be built with an **explicit API origin** (the
    dev-only Vite proxy is not used in production);
  - the production API requires **narrowly scoped CORS**, allowing only the
    deployed frontend origin(s), because the API is cross-origin in production
    (development is same-origin via the proxy, so no CORS exists today);
  - SSE behavior must remain compatible with the direct Cloud Run connection
    (long-lived POST stream, cancellation on client disconnect).
- **Neon is the production PostgreSQL.** PostgreSQL is preserved; the relational
  layer is not redesigned. Production uses **PostgreSQL 16 to match local
  development**, loads the real Olist schema and data, and keeps application
  configuration on the existing `POSTGRES_*` concept where practical. Credentials
  and connection strings are supplied through the runtime secret/configuration
  mechanism and are never committed.
- **DeepSeek remains the model provider** (ADR-005). Inference cost is separate
  from hosting cost.
- **No always-on infrastructure is introduced** unless a demonstrated need
  appears. The architecture stays proportional to sporadic traffic.

### Why `/query` is not proxied through Firebase Hosting

Firebase Hosting can rewrite requests to a Cloud Run service, and that is a
legitimate way to expose a Cloud Run backend under a Hosting domain. It is not
used here for one project-specific reason: the Hosting request timeout for
dynamic content (Cloud Functions and Cloud Run) is **60 seconds**, whereas the
OlistIQ API is an SSE endpoint whose `/query` stream may legitimately run longer
than that and must not sit behind a fixed request-timeout/proxy boundary. Keeping
the browser connection direct to Cloud Run avoids that boundary and removes an
unnecessary proxy hop, so the streaming path stays simple. This is a
project-specific choice, not a claim that Hosting rewrites are inherently
unsuitable — an application without a long-lived streaming endpoint might
reasonably choose the proxy. The trade-off accepted here is explicit API-origin
configuration plus production CORS.

### Database

- **Preserve PostgreSQL.** Neon is a managed PostgreSQL, so the application,
  schema, and SQL boundary (ADR-002, ADR-004) are unchanged.
- **Load the real Olist schema and data** into the production database using the
  existing schema and ingestion path; production does not run a reduced or
  synthetic dataset.
- **Do not redesign tables or views for Neon.** No Olist table or view is changed
  to suit the host.
- **Configuration** continues to use the existing `POSTGRES_*` variables where
  practical; production values (host, port, database, user, password) come from
  the runtime secret/configuration mechanism and are never committed.
- **Terraform does not load the Olist dataset.** Application/data provisioning
  remains a separate responsibility from infrastructure lifecycle.

### Deployment, automation, and infrastructure as code are separate concerns

- **Infrastructure architecture** — this ADR: GCP + Firebase Hosting + Cloud Run
  + Neon.
- **Application deployment automation** — future GitHub Actions CD (not defined
  or implemented by this ADR).
- **Infrastructure as code** — future Terraform (not defined or implemented by
  this ADR).

Agreed sequence:

1. Manually deploy and verify the production architecture.
2. Understand actual runtime behavior and operational requirements.
3. Add CD for routine application releases.
4. Add a focused Terraform layer afterward.
5. Import/reconcile the existing infrastructure.
6. Make Terraform the infrastructure source of truth.

Terraform owns **infrastructure lifecycle** only; it does not load the Olist
dataset. CD must not bypass required CI checks (AGENTS.md).

## Alternatives considered

- **Firebase Hosting rewrite/proxy for `/query`.** Rejected. Hosting can proxy to
  Cloud Run, but its 60-second dynamic-content request timeout places the
  long-lived SSE stream behind a fixed proxy boundary for no benefit here, since
  the frontend is fully static and the API is a single service. A direct
  cross-origin call keeps the streaming path simple; the cost is explicit
  API-origin configuration and scoped CORS.
- **Cloud SQL for PostgreSQL.** Considered because it is PostgreSQL-compatible,
  GCP-native, and integrates cleanly with Cloud Run. Rejected for this milestone
  as a combination of **verified product constraints** and **project-specific
  judgement**:
  - *Verified product constraints:* PostgreSQL 16+ Cloud SQL instances default to
    the **Enterprise Plus** edition, while the lower-cost shared-core machine
    series is available only in the **Enterprise** edition. Cloud SQL does not
    provide scale-to-zero: an instance is started and stopped manually, and
    stopping it suspends compute charges but does not eliminate storage (and any
    IP) charges. The Cloud SQL free trial instance is a 30-day,
    one-per-project offering that Google documents as not recommended for
    production (no SLA) and is not the basis for this deployment decision.
  - *Project judgement:* for sporadic traffic and a lean portfolio budget, a
    managed PostgreSQL whose compute scales to zero is a better fit than an
    always-on instance. Neon provides that while remaining standard PostgreSQL,
    so the application and data model are unchanged.
- **An always-on VM / container / service.** Rejected. The idle cost and
  operational overhead are not justified at the current traffic level, and
  scale-to-zero is preferable for this project.
- **Replacing PostgreSQL with a different database architecture.** Rejected.
  PostgreSQL is central to the project (schema introspection and the read-only
  SQL boundary), and the Olist data is inherently relational. Changing the
  datastore would be a major redesign with no benefit.

### PostgreSQL major-version note

Local development uses PostgreSQL 16 (`docker-compose.yml`). Neon supports
PostgreSQL 16, so production will use **PostgreSQL 16**, matching local
development, and **no major-version migration is required for this deployment**.
A PostgreSQL 16 → 18 change would be a major-version migration if considered
later; that is out of scope for this decision and is not implemented.

## Consequences

Positive:

- A small, understandable architecture with low operational overhead.
- Scale-to-zero backend and database, matching sporadic traffic.
- Minimal application and database redesign; the framework-neutral boundaries
  (ADR-004, ADR-006, ADR-010) are preserved.
- A direct browser → Cloud Run SSE path with no extra proxy hop.
- Clear separation between the static frontend, the API runtime, and the
  database, each independently replaceable.

Costs and risks:

- **Cloud Run cold starts:** scale-to-zero adds latency to the first request
  after an idle period.
- **Neon wake latency:** Neon compute suspends after a period of inactivity and
  reactivates on the next query, adding latency to the first request after idle.
- **Cross-provider network path:** the Cloud Run → Neon connection crosses from
  GCP to a non-GCP managed service, adding network latency and one more external
  dependency in the request path. (The browser → Cloud Run segment is a single
  HTTPS path to the API and is not itself a cross-provider hop.)
- **CORS** is a new production configuration surface and must stay narrowly
  scoped to the deployed frontend origin(s).
- **The API remains unauthenticated and rate-limit-free** for now; authentication
  and rate limiting are future hardening items.
- **No high availability** at the current portfolio scale (a single backend
  service and a single database), which is accepted.
- **Dependence on external managed services** (GCP, Firebase, Neon, DeepSeek).
- **The first deployment is intentionally manual** before CD or Terraform.

### Non-goals

ADR-011 does not introduce: authentication, authorization, rate limiting, public
MCP hosting, multi-region deployment, high availability, Kubernetes, a service
mesh, Redis, Kafka, queues, enterprise multi-environment infrastructure, CD
automation, or a Terraform implementation.

## Relationship to existing decisions

This ADR is additive and changes no existing decision. It preserves ADR-004 (the
provider-neutral agent boundary), ADR-006 (the SSE HTTP boundary — the direct
Cloud Run connection preserves the streaming POST contract, and the Vite dev
proxy remains a development-only convenience), ADR-009 (the SQL-timeout budget),
and ADR-010 (opt-in observability). It records the deployment architecture those
boundaries will be hosted on and defers its implementation to later work under
the AGENTS.md deployment rules. Production is not yet deployed; this ADR records
the finalized decision, not a completed deployment.
