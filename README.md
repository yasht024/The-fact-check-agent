# Fact-Checking Web App

The React frontend is served by FastAPI. The Phase 2 API accepts PDFs, queues runs in PostgreSQL, and serves run status to clients with the run’s access token. The Python worker is a configuration check until job processing is added.

## Frontend design

Use the [design brief](docs/problem%20statement.md#google-stitch-design-brief) to design the PDF upload, progress, and report screens in Google Stitch. When the screens are ready, save the Stitch project link or exported screen references in `docs/design/` with any exported `DESIGN.md`. [Google Stitch](https://stitch.withgoogle.com/) · [Google’s `DESIGN.md` guide](https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/)

In [Phase 10](docs/implementationPlan.md#phase-10-build-the-upload-and-report-interface), adapt the chosen screens into React components in `frontend/src/` and connect them to FastAPI. The local setup below runs the Phase 1 React scaffold independently of Stitch.

## Run locally

Use Python 3.11 or newer and Node.js 22.12 or newer with npm. Check `python3 --version`, `node --version`, and `npm --version` before creating the virtual environment. If `python3` points to an older system Python, use the path to your newer Python in the first command.

From the workspace root, create a virtual environment, install the dependencies, and build the frontend.

```sh
python3 -m venv .venv &&
source .venv/bin/activate &&
python -m pip install -r requirements.txt &&
npm --prefix frontend install &&
npm --prefix frontend run build
```

For uploads and report status, [start PostgreSQL for development](#start-postgresql-for-development) first. Then start the web service from the workspace root. FastAPI serves the React build from `frontend/dist/` on the same origin as `/healthz`.

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Keep that terminal running and wait for `Uvicorn running on http://127.0.0.1:8000` before opening the app. If the browser reports `ERR_CONNECTION_REFUSED`, check the terminal for the first setup or startup error. If port 8000 is already in use, start Uvicorn with `--port 8001` and use port 8001 for both the app and the health check.

For frontend-only development, run `npm --prefix frontend run dev` and open the URL printed by Vite. Build again to see frontend changes through FastAPI. If the initial build was missing when FastAPI started, restart the service after building.

To run the worker entry point, activate the same virtual environment and run the following from the workspace root. The worker validates its environment settings, prints a startup message, and exits.

```sh
python -m backend.app.processing.worker
```

## Environment variables

Set these variables in the shell that starts the service or worker. The upload and report routes use `DATABASE_URL`. The React page, health endpoint, and worker startup check work without a database. Phase 2 requires no provider credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Unset | PostgreSQL connection URL. |
| `BRAVE_SEARCH_API_KEY` | Unset | Brave Search credential. |
| `OPENAI_API_KEY` | Unset | OpenAI credential. |
| `MAX_UPLOAD_BYTES` | `10485760` | Maximum PDF size in bytes (10 MiB). |
| `MAX_PDF_PAGES` | `30` | Maximum PDF page count. |
| `REPORT_RETENTION_HOURS` | `24` | Hours a report stays available after a run finishes. |

The limits take positive integers. Phase 2 enforces `MAX_UPLOAD_BYTES`. Page count and report retention are handled in later phases.

## Phase 1 acceptance check

In a clean shell, leave `DATABASE_URL`, `BRAVE_SEARCH_API_KEY`, and `OPENAI_API_KEY` unset. Build and start FastAPI as shown above, then open `http://127.0.0.1:8000/` in a browser and confirm the React page displays the **Fact-Checking Web App** heading.

```sh
curl -i http://127.0.0.1:8000/healthz
```

The health endpoint should return HTTP `200` and `{"status":"ok"}`.

### Latest verification

| Check | Result |
| --- | --- |
| Local tools | The virtual environment reports Python 3.12.14 and pip 25.0.1. Bundled Node.js 24.22.3 and pnpm 11.25.0 run. `npm` is unavailable on the shell `PATH`. |
| Worker and source checks | Passed with the database and provider variables unset. Verified configuration defaults, overrides, invalid limits, Python and JavaScript syntax, and the frontend entry point. |
| React page | Passed. The React page loaded through FastAPI locally. |
| `GET /healthz` | Passed. The local endpoint returned HTTP `200` and `{"status":"ok"}`. |

## Start PostgreSQL for development

With Docker Compose available, run these commands from the workspace root. The development database uses PostgreSQL 17 and listens on `127.0.0.1:55432`. Compose applies the SQL migrations when it initializes the database volume.

```sh
docker compose up -d --wait postgres &&
export DATABASE_URL='postgresql://factcheck:factcheck_dev@127.0.0.1:55432/factcheck'
```

Run FastAPI from the shell with `DATABASE_URL` set. Use `docker compose stop postgres` to stop the database and keep its data.

If you already have a local PostgreSQL database, set `DATABASE_URL` to its connection URL and apply the migration once before starting FastAPI.

```sh
psql -X "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/001_runs_and_uploads.sql
```

## Phase 2 API and acceptance checks

Send one PDF as the multipart `file` field to `POST /api/runs`. The API returns `202` with `run_id` and `access_token` after PostgreSQL commits the queued run and PDF together. A file without the `%PDF-` signature returns `400`, and a file above `MAX_UPLOAD_BYTES` returns `413`.

Send `Authorization: Bearer <access_token>` to `GET /api/runs/<run_id>`. The report returns `run_id`, `state`, and `stage`. A missing or incorrect token returns `404`. PostgreSQL stores the SHA-256 hash of the token.

See [Phase 2 acceptance checks](docs/evals.md#phase-2-accept-and-store-a-pdf) for the test command, expected results, and verification status.

### Run Phase 2 tests on GitHub

1. On GitHub, create a [new repository](https://github.com/new) and select **Private**. Leave the README, `.gitignore`, and license options unset so the repository starts empty. Copy its remote URL from **Quick Setup**. [GitHub’s import guide](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
2. From the project root, initialize Git and review the staged files. The project’s `.gitignore` excludes `.env` files, the virtual environment, and `local-notes/`.

   ```sh
   git init -b main
   git add -- .gitignore .github README.md backend compose.yaml docs frontend requirements.txt
   git diff --cached --name-only
   ```

3. Commit and push from a terminal authenticated with GitHub. Replace `OWNER/REPO` with the owner and repository name from the HTTPS remote URL. [GitHub’s authentication guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github)

   ```sh
   git commit -m "Add Phase 2 project and acceptance tests"
   git remote add origin https://github.com/OWNER/REPO.git
   git push -u origin main
   ```

4. Open **Actions** → **Phase 2 acceptance tests** → the run for your push → **PostgreSQL acceptance** → **Run Phase 2 tests**. A passing run shows `Ran 6 tests` and `OK` with zero skips. [GitHub’s guide to workflow logs](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs)

The [workflow](.github/workflows/phase-2.yml) uses Python 3.12 and a fresh PostgreSQL 17 service on a GitHub-hosted Ubuntu runner. It sets `TEST_DATABASE_URL` from that service’s assigned port. The test database needs no GitHub secrets.
