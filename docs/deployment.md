# Production deployment

OlistIQ's production deployment is **live**. This document records the deployed
state, the verified configuration, and the checks that remain outstanding.

The architecture decision is recorded separately in
[ADR-011](../adr/ADR-011-gcp-neon-deployment-architecture.md); this runbook
documents the implementation. Deployment was performed **manually**; GitHub
Actions CD and Terraform remain deferred (see `AGENTS.md`).

Throughout this document:

- **Verified** — observed live via read-only `gcloud` inspection during the
  2026-10-10 audit.
- **Historical** — setup context, not part of the current live request path.
- **Unverified** — could not be independently confirmed read-only.

## Architecture

```text
Browser → Firebase Hosting (static React/Vite SPA)

Browser → direct HTTPS/SSE → Cloud Run (FastAPI / LangGraph) → Neon PostgreSQL
                                                             → DeepSeek API
```

Firebase Hosting serves only the static assets. The SPA calls the Cloud Run
service **directly** over HTTPS/SSE — `/query` is not proxied through Hosting —
so the frontend is built with an explicit API origin and the backend allows that
origin through CORS.

## Identifiers and URLs

| Item | Value |
| --- | --- |
| GCP project | `olistiq-prod-2026` (number `757109588480`) |
| Region | `asia-southeast1` |
| Cloud Run service | `olistiq-api` |
| Firebase Hosting URL | [https://olistiq-prod-2026.web.app](https://olistiq-prod-2026.web.app) |
| Canonical API URL | [https://olistiq-api-757109588480.asia-southeast1.run.app](https://olistiq-api-757109588480.asia-southeast1.run.app) |

## Current revision

- Ready revision: **`olistiq-api-00003-ksw`**, receiving **100%** of traffic
  (latest ready). *(Verified)*
- Deployed image digest:
  `sha256:b36cc7c51defbb22b043dcecd568332f0928054c253b4b61c1e024ac4c323c4f`.
  *(Verified)*
- Runtime service account:
  `olistiq-cloud-run@olistiq-prod-2026.iam.gserviceaccount.com`. *(Verified)*

## Cloud Run configuration *(Verified)*

| Setting | Value |
| --- | --- |
| CPU | 1 |
| Memory | 1 GiB |
| Container concurrency | 5 |
| Request timeout | 300 seconds |
| Minimum instances | 0 (scale to zero) |
| Startup CPU boost | enabled |

### Scaling maximum

The effective maximum is **3 instances**. This is the **service-level** limit
(`run.googleapis.com/maxScale = 3`), which applies because Cloud Run caps at the
lower of the service-level and revision-level limits. The revision template
value `autoscaling.knative.dev/maxScale = 20` is retained; with the service-level
maximum at 3, the effective limit is 3.

## Environment and secrets *(Verified; names only)*

Environment variables: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_HOST`,
`POSTGRES_PORT`, `PGSSLMODE`, `PGCHANNELBINDING`, `PGGSSENCMODE`,
`CORS_ALLOWED_ORIGINS`. Values are intentionally not recorded here.

- `CORS_ALLOWED_ORIGINS` = `https://olistiq-prod-2026.web.app`.
- `POSTGRES_PASSWORD` is provided from Secret Manager.
- `DEEPSEEK_API_KEY` is provided from Secret Manager.

Secret Manager references (names and versions only; **values are never
documented**):

| Environment variable | Secret | Version |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `neon-db-password` | 1 |
| `DEEPSEEK_API_KEY` | `deepseek-api-key` | 1 |

## Database

Production uses **Neon PostgreSQL 16**. The framework-neutral database and SQL
boundary is unchanged; the application configuration continues to use the
existing `POSTGRES_*` concept, with the password supplied from Secret Manager. No
schema redesign was introduced for the hosted database.

## Frontend hosting

The React/Vite SPA is served by Firebase Hosting. It is built with a public,
non-secret build-time API origin (`VITE_API_ORIGIN`); Hosting does not rewrite
`/query` to the backend.

## Verification performed

- **Build (Historical):** Cloud Build `1765a0b8-9c34-4d7a-a3e6-e79b7f52b8ac`
  succeeded (2026-10-09, ~22:05–22:06 UTC). It produced the image tagged
  `c4ec320`, whose digest matches the deployed digest above.
- **CORS preflight (Verified):** a live `OPTIONS /query` request returned
  `200`.
- **SSE request (Verified):** a live `POST /query` request completed with `200`
  (the streaming response finished normally).
- **Real browser (Historical):** a real-browser analytical query succeeded
  through the deployed frontend before this audit.
- **Log window (Verified):** the 24-hour window ending at the 2026-10-10 audit,
  filtered to `resource.type="cloud_run_revision"` and
  `resource.labels.service_name="olistiq-api"`, contained **no severity-ERROR
  entries and no HTTP 5xx request logs**, and the revision started cleanly
  (Uvicorn listening on `0.0.0.0:8080`, default startup TCP probe succeeded).
  This is **not** a guarantee of error-free operation outside the inspected
  window.

## Telemetry

GenAI message-content capture is pinned to `NO_CONTENT` in the application
configuration, and production telemetry is currently **inert**: no OTLP exporter
or console telemetry settings are configured, so no providers are created.

## Known limitations (unverified)

- **Client-disconnect cancellation** has not been independently verified with an
  ASGI disconnect test. The design intent is that a client disconnect terminates
  the SSE stream, but this has not been exercised by an automated test.
- **Budget-alert settings could not be verified** in this audit because of
  billing permissions/quota-project limitations. No claim is made about current
  spending or future costs.
- **Cold-start latency, instance-count metrics, and related Cloud Monitoring
  time series were not independently verified.** Request status/latency are
  visible in Cloud Run request logs; scaling and cold-start metrics are not
  covered by this audit.

## Operational trade-offs

- **Scale-to-zero** keeps idle cost near zero at the expense of cold starts;
  startup CPU boost is enabled to reduce startup latency.
- **Neon wake latency** and the **cross-provider network path** from Cloud Run to
  Neon add latency relative to a same-region, same-provider database.
- **No high availability** at the current portfolio scale (a single backend
  service and a single database).
- The deployment **depends on managed external services** (Google Cloud,
  Firebase, Neon, DeepSeek).
- **The deployment is manual.** GitHub Actions CD and Terraform are deferred.

## References

- [`adr/ADR-011-gcp-neon-deployment-architecture.md`](../adr/ADR-011-gcp-neon-deployment-architecture.md) — deployment architecture decision.
- [`AGENTS.md`](../AGENTS.md) — CI/CD and deployment workflow rules.
