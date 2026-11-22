# Proofline — PDF Fact-Checking Agent

Proofline reviews factual claims in a PDF against live web sources. It keeps the original claim and page number, explains the result, and links to the evidence. When a check cannot finish, it says why instead of inventing a verdict.

**[Try the live app](https://proofline-omega-nine.vercel.app)** · **[API health](https://api-production-7966543a.up.railway.app/healthz)** · **[Architecture](docs/architecture.md)** · **[Test results and known gaps](docs/evals.md#phase-11-railway-and-vercel-deployment)**

## Try it in two minutes

1. Open the live app and choose a small PDF with selectable text.
2. Select **Start fact-check**. Progress and saved findings appear as the worker processes the document.
3. Read each finding and open its citations. Compare the evidence with the claim's date, units, and scope.
4. Keep the same tab open. Refreshing restores the report using a token in session storage. Reports expire 24 hours after completion by default.

No account is required to upload a PDF. The supported input limits are **10 MiB and 30 pages**, configured on the server. Small uploads have passed the public workflow; large-upload transport tests have timed out, so full 10 MiB hosted support is still unverified. Scanned/image-only PDFs require OCR before uploading.

## What a report shows

Each finding includes the original excerpt, one-based PDF page number, explanation, any supported correction and reporting period, and clickable citations. Findings stay in document order. Empty documents, skipped pages, pending claims, incomplete checks, and failed PDFs have separate states.

| Result | Meaning |
| --- | --- |
| **Verified** | Retrieved evidence supports the claim as written, including its period and scope. |
| **Inaccurate** | A detail is wrong or a current figure is outdated. A correction appears only when the evidence supports it. |
| **False** | Evidence refutes the premise, or a completed initial and follow-up search finds no credible support. The explanation distinguishes these cases. |
| **Check incomplete** | A timeout, inaccessible material source, unresolved conflict, or exhausted processing budget prevents a verdict. This is not a False result. |

A newer release does not automatically invalidate a correctly dated historical claim. For example, a 2024 revenue figure can remain Verified while using that same figure as the latest annual revenue can be Inaccurate.

## How it works

```mermaid
flowchart LR
    Browser[React frontend on Vercel] -->|Upload and poll through API routes| API[FastAPI on Railway]
    API -->|Queue PDF and read report| DB[(PostgreSQL)]
    Worker[Python worker on Railway] <-->|Lease jobs and save findings| DB
    Worker --> PDF[pypdf text extraction]
    Worker --> Gemini[Gemini: claims and assessments]
    Worker --> Tavily[Tavily: source discovery]
    Worker --> Sources[Public HTML and PDF evidence]
```

1. **Accept the file.** FastAPI validates the PDF signature and byte limit, then stores the run and staged PDF together. It returns HTTP 202 with a run ID and an unguessable report token.
2. **Extract claims.** The worker parses page-numbered text with `pypdf`. Gemini proposes structured factual claims. Local validation checks that each excerpt really occurs on the stated page and removes duplicates.
3. **Gather evidence.** Tavily discovers candidate sources. The worker retrieves the actual pages or PDFs; search snippets are not treated as evidence. It targets a recognized original publisher first and broadens a follow-up when evidence is weak.
4. **Assess and validate.** Gemini compares the claim with retrieved passages. Local checks validate citation IDs and conservative numeric correction/value/unit/period relationships before publishing a finding.
5. **Save and display.** Each finding and its evidence commit together. React polls for saved progress and preserves the last findings during temporary connection errors.

FastAPI also serves the built React assets for local use and as a Railway fallback. In production, Vercel serves the frontend and forwards `/api/*` and `/healthz` to Railway, so the browser keeps using same-origin API URLs.

### Design choices worth discussing in an interview

| Choice | Reason and trade-off |
| --- | --- |
| Separate API and worker | Upload requests finish quickly; parsing and provider calls can take longer. A separate worker must stay running. |
| PostgreSQL as queue and report store | A transaction can save the job and its PDF together without another queue service. Workers poll the database; concurrency is controlled by worker count. |
| Leases and saved checkpoints | A replacement worker can resume after lease expiry without duplicating committed findings. External calls may repeat if a process crashes before committing. |
| Model output plus local validation | The model handles language and evidence assessment; code checks excerpts, citation membership, and supported numeric corrections. Semantic truth and source credibility still depend on model judgment. |
| Explicit incomplete results | Provider failure is not evidence that a claim is false. Some reports finish with useful findings and clearly marked gaps. |
| Same-tab bearer access | Reviewers need no account. The token is kept in session storage and only its hash is stored in PostgreSQL; cross-device recovery is not provided. |

Source retrieval rejects private DNS answers and revalidates redirects. Connections pin a validated public address and verify TLS against the original hostname. IPv4 preference and bounded address fallback support deployments without IPv6 egress. Response sizes, redirects, parser resources, processing time, and provider attempts are bounded.

## Technology and repository map

| Area | Technology / location |
| --- | --- |
| Interface | React 19, Vite 8, plain CSS — `frontend/src/` |
| HTTP API and report access | FastAPI — `backend/app/main.py`, `runs.py`, `run_store.py` |
| Processing pipeline | Python — `backend/app/processing/` |
| PDF parsing | `pypdf`, isolated parser subprocess |
| Claim extraction and assessment | Gemini structured output, shared key/model |
| Source discovery | Tavily Search API |
| Queue and persistence | PostgreSQL — `backend/migrations/001`–`003` |
| Deployment | `Dockerfile`, `deploy/*.settings.json`, `scripts/`, `vercel.json` |
| Automated tests | `backend/tests/`, `frontend/src/api.test.js`, `scripts/build-vercel.test.mjs` |
| Design reference | [Chosen desktop/mobile Stitch exports](docs/Design/stitch_fact_checking_web_app/) |

## Run locally

### 1. Install dependencies and build React

Use **Python 3.12**, **Node.js 24**, and **pnpm 10.24.0** through Corepack. You also need PostgreSQL; Docker Compose is the easiest option below. Run these commands from the repository root unless a step says otherwise.

```sh
git clone https://github.com/yasht024/The-fact-check-agent.git
cd The-fact-check-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm run build
cd ..
```

The repository is private, so cloning requires access. If `corepack` is unavailable, install pnpm 10.24.0 with your Node package manager, then use `pnpm` in place of `corepack pnpm`.

### 2. Start a development database

```sh
docker compose up -d --wait postgres
export DATABASE_URL='postgresql://factcheck:factcheck_dev@127.0.0.1:55432/factcheck'
```

Those credentials are for the local development database only. Compose uses PostgreSQL 17 and applies migrations 001–003 when it creates a fresh volume. Stop it with `docker compose stop postgres` to retain data. Existing volumes do not reapply migrations automatically.

**Alternative: a fresh PostgreSQL database outside Compose.** Set its `DATABASE_URL` privately in your environment, then run:

```sh
python -m backend.migrate
```

The runner applies these migrations in order and records their checksums:

| Migration | Adds |
| --- | --- |
| `001_runs_and_uploads.sql` | Run records and staged PDF bytes |
| `002_worker_leases.sql` | Worker leases, retries, and queue index |
| `003_findings.sql` | Claims, checks, evidence, budgets, coverage gaps, and report expiry |

Do not mix initialization paths. The runner safely reruns against databases it tracks, but refuses an existing schema created by raw SQL or Compose without its ledger. Review migration history before adopting an older database; do not reset an existing database to bypass that check.

### 3. Configure credentials privately

Set `DATABASE_URL` in both API and worker terminals. Set `GEMINI_API_KEY` and `TAVILY_API_KEY` in the worker terminal. The app does not automatically read `.env` files. If you use a local `.env` that you created, load it in each relevant shell with tracing disabled:

```sh
set -a
source .env
set +a
```

Keep credentials out of Git, chat, screenshots, and logs. `.env` files, CLI state, local notes, generated output, and build artifacts are excluded from Git. Provider/database credentials must never be put in `VITE_*` variables or the frontend build.

### 4. Run API and worker in separate terminals

**Terminal A — API**, with the virtual environment active and `DATABASE_URL` configured:

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

**Terminal B — worker**, with the same virtual environment/database and provider keys configured:

```sh
python -m backend.app.processing.worker
```

Open **http://127.0.0.1:8000** after Uvicorn reports that it has started. A running API without a worker accepts uploads but leaves them queued. `/healthz` checks API process liveness; it does not prove that the database, worker, or providers are healthy.

Rebuild React after frontend changes to see them through FastAPI. For the Vite development server, run `corepack pnpm run dev` inside `frontend/`; full upload testing should use the FastAPI-served build unless you configure an API development proxy.

### Configuration reference

| Variable | Default | Used by |
| --- | --- | --- |
| `DATABASE_URL` | Required for runs | API, worker, migration runner |
| `GEMINI_API_KEY` | Required for live processing | Worker only |
| `TAVILY_API_KEY` | Required for live processing | Worker only |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Worker extraction and assessment |
| `MAX_UPLOAD_BYTES` | `10485760` (10 MiB) | API; advertised by `/api/config` |
| `MAX_PDF_PAGES` | `30` | Worker; advertised by `/api/config` |
| `REPORT_RETENTION_HOURS` | `24` | API/worker report access and cleanup |
| `PROCESSING_DEADLINE_SECONDS` | `600` | Worker, including queue time |
| `MAX_CLAIMS` | `50` | Worker |
| `MAX_SEARCHES_PER_CLAIM` | `4` | Worker |
| `PORT` | `8000` locally | Deployment API entry point; Railway supplies it |
| `RAILWAY_API_ORIGIN` | Actual public Railway HTTPS origin | Vercel build only; public configuration |

Use matching upload/page/retention settings on API and worker. The staged PDF is deleted when claim extraction commits or ends in a terminal failure; reports are removed after expiry by the worker's cleanup pass. Hosting backups have their own retention policy.

## API overview

| Endpoint | Purpose |
| --- | --- |
| `POST /api/runs` | Multipart `file` upload; returns HTTP 202, `run_id`, and `access_token` |
| `GET /api/runs/{run_id}` | Progress/report; send `Authorization: Bearer <access_token>` |
| `GET /api/config` | Public byte/page limits and retention hours |
| `GET /healthz` | API process liveness |

An unavailable report or invalid token returns 404. Invalid input returns 400; an oversized file returns 413 when rejected by the API. Report responses use `Cache-Control: no-store`. Tokens are never included in report URLs.

## Tests and verified behavior

Automated tests simulate provider responses and use real PostgreSQL for persistence/recovery checks. They do not spend model/search credits.

```sh
# Use a disposable local database; tests create and remove isolated schemas.
TEST_DATABASE_URL='postgresql://factcheck:factcheck_dev@127.0.0.1:55432/factcheck' \
  python -m unittest discover -s backend/tests -t . -v

cd frontend
corepack pnpm test
cd ..
node --test scripts/build-vercel.test.mjs
```

The latest local verification passed **110 backend tests and seven frontend/deployment tests**. Coverage includes upload validation, lease recovery, atomic persistence, PDF failure states, excerpt validation, search failures, source-address/TLS protections, verdict/correction rules, migrations, and deployment routing. GitHub Actions runs the backend suite against PostgreSQL 17; frontend checks run separately in the application workflow.

Live-provider checks are deliberately opt-in. For example:

```sh
python -m backend.tests.live_deployment \
  --base-url https://proofline-omega-nine.vercel.app --fixture nasa
```

This uploads a synthetic PDF, polls the public report, and attempts to reproduce citations using the strict local source retriever. It never prints or saves the report token. It is an HTTP test, not a browser test. The cloud NASA run completed Verified; its strict local citation re-fetch timed out, while an independent normal HTTPS request confirmed the NASA passage. These outcomes are recorded separately in [docs/evals.md](docs/evals.md).

## Deployment and GitHub workflow

The current topology is **Vercel frontend + Railway API, worker, and PostgreSQL**. It replaces the original Render plan. The PostgreSQL 18 Railway template has persistent storage and no new public database endpoint.

| Service | Build / release | Start |
| --- | --- | --- |
| Railway API | Root Dockerfile; pre-deploy `python -m backend.migrate` | `python -m backend.deploy web` |
| Railway worker | Same Dockerfile and migration gate | `python -m backend.deploy worker` |
| Vercel frontend | Root `vercel.json`: frozen pnpm install, React build, Build Output API generation | Static CDN; no Python process |

The Docker image builds React and copies `frontend/dist` into the Python runtime. Both Railway services run as an unprivileged user. The worker has one continuously running replica, no public port, and no HTTP healthcheck. API binds `0.0.0.0:$PORT` and uses `/healthz` as its deployment healthcheck.

For another Railway environment, create API/worker/PostgreSQL services and configure private database references on both app services. Configure Gemini/Tavily keys on the worker only. Apply the reviewed settings with the authenticated CLI:

```sh
python scripts/configure_railway.py --role api --service API_SERVICE_ID --environment ENVIRONMENT_ID
python scripts/configure_railway.py --role worker --service WORKER_SERVICE_ID --environment ENVIRONMENT_ID
railway up --service api --environment production --detach
railway up --service worker --environment production --detach
```

Set `RAILWAY_CLI` to the executable path if it is not on PATH. The script uses Railway's current service-update API; it does not rely on deprecated `railway.json` configuration paths.

For Vercel, keep the project root at the repository root, Node 24, and the framework preset **Other**. Set `RAILWAY_API_ORIGIN` to the API's public HTTPS origin in Production/Preview build environments. `scripts/build-vercel.mjs` creates static assets and external API/health routes without a Vercel Function. Database/provider credentials stay on Railway.

**GitHub connection:** the intended production branch is `main`. Once the Vercel Git integration is connected, pushes/merges to `main` build the frontend automatically; other branches get previews. The connection status and latest checked commit are recorded in [the publication results](docs/evals.md#github-publication-and-vercel-git-connection). Frontend automation does **not** deploy Railway: API/worker changes still require the Railway commands above. Preview frontends currently use the same Railway API; use a separate backend environment before testing incompatible API or database changes.

Review changes, run tests, and use a feature branch/PR for future work. A direct production-branch push can publish frontend changes immediately; GitHub Actions results and Vercel deployment status should both be checked.

## Current status and limitations

- **Observed public success:** a small PDF reached a persisted Verified finding for Earth's equatorial diameter; its actual [NASA citation](https://science.nasa.gov/earth/facts/) contains the supporting figure. Public health, configuration, HTML, JavaScript, and CSS requests passed.
- **Browser acceptance pending:** the hosted fresh-session upload/refresh/citation check has not been recorded. Local Phase 10 desktop/mobile and keyboard checks passed.
- **Large uploads unverified:** hosted 10 MiB boundary requests timed out. The same oversized input was correctly rejected in-process. The transport cause is unresolved.
- **Text-based PDFs only:** no OCR or JavaScript-rendered source retrieval. Unreadable pages are reported as coverage gaps.
- **Model and source limits:** structured validation does not establish general fact-checking accuracy. Paywalls, blocked sources, conflicting evidence, ambiguous periods, or provider outages can leave checks incomplete. Numeric unit conversion is deliberately conservative.
- **Report access is temporary:** same-tab session storage and expiry are intentional; there is no account-based report library or cross-device recovery.

Phase 11 is deployed, with the acceptance gaps above. Phase 12's broader deployed evaluation, demo, and submission package remain unfinished.

## Project documents

| Document | Read it for |
| --- | --- |
| [Problem statement](docs/problem%20statement.md) | Product scope and intended workflow |
| [Architecture](docs/architecture.md) | Components, contracts, recovery, and validation design |
| [Implementation plan](docs/implementationPlan.md) | Phase boundaries and completion criteria |
| [Conventions](docs/conventions.md) | Naming, data, security, and engineering rules |
| [Edge cases](docs/edgecases.md) | Expected behavior on difficult or failed checks |
| [Evaluations](docs/evals.md) | Actual automated/live results, failures, and unrun checks |
