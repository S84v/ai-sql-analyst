# Production deployment

OlistIQ's production deployment is **live**. This document records the deployed
state, the verified configuration, and the checks that remain outstanding.

The architecture decision is recorded separately in
[ADR-011](../adr/ADR-011-gcp-neon-deployment-architecture.md); this runbook
documents the implementation. The original production deployment was performed
**manually**; routine releases are now automated by GitHub Actions, while
Terraform remains deferred (see `AGENTS.md`).

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

- Ready revision: **`olistiq-api-00005-p6b`**, the latest created and ready
  revision, healthy and receiving **100%** of traffic. *(Verified)*
- Requested image — the **image index** digest recorded on the service template:
  `sha256:e5be36760d3357fe2c0441519629dd23d1487881118c7ea0e7116580244bdcf4`.
  *(Verified)*
- Served image — the **`linux/amd64` platform-manifest** digest Cloud Run resolved
  onto the ready revision:
  `sha256:7da290048a49bc48686d128fa856cfecbf157370fc9ebb93ad4a7869b34fe9d4`.
  *(Verified)* The two digests differ by design: the first is the multi-manifest
  image index that was pushed, the second is the platform-specific manifest Cloud
  Run actually runs; see [Backend deployment](#backend-deployment).
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

- **Original manual build (Historical):** Cloud Build
  `1765a0b8-9c34-4d7a-a3e6-e79b7f52b8ac` succeeded (2026-10-09, ~22:05–22:06 UTC),
  producing the image tagged `c4ec320`. This predates automated CD and is kept for
  context only; it is **not** the currently served image.
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
- **Deployment automation:** routine application releases deploy through GitHub
  Actions (see below); Terraform remains deferred.

## Automated deployment (GitHub Actions)

Routine releases are automated. Pull requests run checks only; production
deploys happen on pushes to `main` or by a manual dispatch, and **never from pull
requests**.

The workflows are **implemented and operational**. The following successful
verification runs confirm both components deploy (all on `main`):

| Run | Trigger | Commit | Result |
| --- | --- | --- | --- |
| [Backend deployment #2](https://github.com/S84v/ai-sql-analyst/actions/runs/38006945830) | `workflow_dispatch` | `fa789811f812d7ad5bf5df6c747d8810738b731c` | success (test + deploy jobs) |
| [Frontend deployment #1](https://github.com/S84v/ai-sql-analyst/actions/runs/38005626855) | `workflow_dispatch` | `47b3b80f3442f7897f36cf0426fe836461ab2338` | success (test + deploy jobs) |
| [CI #52](https://github.com/S84v/ai-sql-analyst/actions/runs/38006925571) | `push` | `fa789811f812d7ad5bf5df6c747d8810738b731c` | success (backend + frontend checks) |

These are **verification runs, not a complete history of every run**. The one-time
Workload Identity Federation / GitHub configuration below is required for these
workflows and is now in place.

| Workflow | Runs on | Deploys |
| --- | --- | --- |
| `ci.yml` | pushes to `main` and pull requests targeting `main` | nothing (backend + frontend checks) |
| `deploy-backend.yml` | push to `main` touching `backend/**`, or manual dispatch | backend image → Cloud Run |
| `deploy-frontend.yml` | push to `main` touching `frontend/**`, `firebase.json`, or `.firebaserc`, or manual dispatch | static SPA → Firebase Hosting |

Deployment is **component-scoped**: a backend-only change does not redeploy
Hosting, and a frontend-only change does not create a new backend revision.
Documentation-only changes deploy nothing. Manual runs are started from `main`
via **Actions → (workflow) → Run workflow**; a guard refuses deployment from any
other ref.

### Authentication

Both deploy workflows authenticate with **Workload Identity Federation** — no
service-account JSON keys and no long-lived tokens are stored in GitHub. Each
workflow impersonates its own deployment service account, scoped to this
repository, the `main` branch, and the specific workflow file:

- `github-deploy-backend@olistiq-prod-2026.iam.gserviceaccount.com` — push the
  image to the `olistiq-backend` Artifact Registry repository, update the
  `olistiq-api` Cloud Run service, and act as the `olistiq-cloud-run@…` runtime
  service account.
- `github-deploy-frontend@olistiq-prod-2026.iam.gserviceaccount.com` — publish to
  Firebase Hosting.

### One-time prerequisites

Configured once, outside the repository (not part of a deploy):

1. A Workload Identity pool and GitHub OIDC provider scoped to
   `S84v/ai-sql-analyst` and `refs/heads/main`.
2. The two deployment service accounts above with least-privilege role bindings
   (no project Owner/Editor).
3. Repository Actions **variables** (non-secret): `GCP_PROJECT_ID`,
   `GCP_REGION`, `GCP_PROJECT_NUMBER`, `GCP_WIF_PROVIDER`, `GCP_BACKEND_SA`,
   `GCP_FRONTEND_SA`, `CLOUD_RUN_SERVICE`, `AR_IMAGE`, `FIREBASE_PROJECT_ID`.

### Backend deployment

The workflow runs the backend tests, builds the image from `backend/` with Docker
Buildx, pushes it to Artifact Registry tagged with the commit SHA, and updates
Cloud Run with an **image-only** change (`gcloud run services update … --image`).
Deploying by immutable digest keeps the revision traceable to its commit, and the
image-only update preserves the runtime service account, CPU/memory, concurrency,
timeout, scaling limits, ingress, environment variables, CORS origin, and Secret
Manager references. The job fails if any of those drift.

The job then verifies the ready revision, the deployed digest, 100% traffic, the
preserved settings, and a CORS preflight (`OPTIONS /query`, which does not invoke
the model). It never calls DeepSeek and never posts to the analytical path.

Buildx pushes an image index containing a runnable `linux/amd64` image
manifest descriptor and associated attestation manifests. The service
template records the index digest, while Cloud Run records the resolved
platform-manifest digest on the ready revision, so the two are expected to
differ.

For the current revision, the index digest is
`sha256:e5be36760d3357fe2c0441519629dd23d1487881118c7ea0e7116580244bdcf4`,
and the resolved platform-manifest digest is
`sha256:7da290048a49bc48686d128fa856cfecbf157370fc9ebb93ad4a7869b34fe9d4`.

The workflow verifies that the served revision digest matches the runnable
`linux/amd64` child-manifest descriptor in the exact index pushed by that
build, excluding attestation descriptors and other architectures. It does
not accept a digest merely because an image exists elsewhere in the
repository.


### Frontend deployment

The workflow runs the frontend checks, builds the SPA, and publishes it to
Firebase Hosting. The Firebase CLI is pinned (`firebase-tools@15.33.0`, after a
known WIF/ADC regression in 15.22.2); a read-only preflight confirms ADC
authentication and Hosting access before publishing, and the Hosting URL is
checked after. There is intentionally **no fallback** to a long-lived token.

### Rollback

Rollback is manual:

- Backend — shift traffic back to the previous revision:
  `gcloud run services update-traffic olistiq-api --region=asia-southeast1 --project=olistiq-prod-2026 --to-revisions=<previous-revision>=100`
- Frontend — open the project's **Hosting release history** in the Firebase
  Console and select **Roll back** on the desired previous release.

## References

- [`adr/ADR-011-gcp-neon-deployment-architecture.md`](../adr/ADR-011-gcp-neon-deployment-architecture.md) — deployment architecture decision.
- [`AGENTS.md`](../AGENTS.md) — CI/CD and deployment workflow rules.
