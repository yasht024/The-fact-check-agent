# Acceptance Checks

## Phase 2. Accept and Store a PDF

Start the [development database](../README.md#start-postgresql-for-development) and activate the project virtual environment. For the Compose database, verify both tables in its `public` schema.

```sh
docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U factcheck -d factcheck \
  -c "SELECT 'public.runs'::regclass AS runs, 'public.uploads'::regclass AS uploads;"
```

Use a database connection with permission to create a schema. From the workspace root, run the API suite against that database.

```sh
python -m pip install -r requirements.txt httpx==0.28.1 &&
TEST_DATABASE_URL="$DATABASE_URL" python -m unittest backend.tests.test_runs -v
```

The suite generates a small one-page PDF and sets the upload limit to its exact byte count. Database tests apply the migration in a temporary schema and remove that schema after the run.

| Scenario | Input | Pass criteria |
| --- | --- | --- |
| Queue a run | Upload the valid PDF at the byte limit. | `POST /api/runs` returns `202`, a run ID, and an access token. PostgreSQL holds the matching PDF and SHA-256 token hash. The correct bearer token returns `200` with `state` and `stage` both `queued`. |
| Reject invalid input | Omit the file, use the wrong field, submit two files, or upload plain text labeled as a PDF. | Return `400 invalid_pdf` without starting a database write. |
| Reject oversized input | Upload a file one byte above the limit or stream an oversized body without `Content-Length`. | Return `413 upload_too_large` without starting a database write. |
| Restrict report access | Request the queued run with a missing, incorrect, or different run’s token. | Return `404 report_unavailable` without revealing the run state. |
| Roll back a failed write | Add a test constraint that rejects the upload insert after the run insert. | Return `503 database_unavailable`. Queries for the failed run ID find no row in either `runs` or `uploads`. |

Phase 2 passes when all six tests succeed with zero skips, including the rollback test against PostgreSQL 17.

### GitHub Actions

The [Phase 2 workflow](../.github/workflows/phase-2.yml) runs on pushes, pull requests, and manual dispatch. It uses Python 3.12 on a GitHub-hosted Ubuntu runner and starts a fresh PostgreSQL 17 service for each job. The test step sets `TEST_DATABASE_URL` to that service. The suite applies the migration to a temporary schema.

In the repository’s **Actions** tab, open **Phase 2 acceptance tests**, select a run, and expand **PostgreSQL acceptance** → **Run Phase 2 tests**. A passing run reports six tests and zero skips, including the forced second-insert rollback check.

### Latest results

| Check | Result |
| --- | --- |
| Development configuration | Passed YAML parsing and checked that the database URL, loopback port, and migration mount match. |
| API behavior in process | Passed the scenarios above with multipart and database test doubles. |
| Compose startup and table check | Pending. Both Docker commands returned `command not found: docker`. The shell also denied the connection to `127.0.0.1:55432`. |
| Local acceptance suite | Blocked. PyPI resolution failed, and the local suite stopped at the missing `psycopg` import. |
| GitHub Actions | **Passed** on November 22, 2026, for `main` commit `d0c201c652ddda8c27710b39347ea5a15f5248d2`. The Ubuntu job ran Python 3.12 against PostgreSQL 17. All six tests passed with zero skips, including `test_failed_upload_insert_rolls_back_run_and_upload`. [Workflow run #2](https://github.com/yasht024/The-fact-check-agent/actions/runs/45049457063) · [Test log](https://github.com/yasht024/The-fact-check-agent/actions/runs/45049457063/job/137291141682#step:6:15) |

## Phase 3. Pick Up and Recover Jobs

Set `TEST_DATABASE_URL` to a PostgreSQL 17 database where the test role can create schemas. The suite creates a fresh schema for each case, inserts a queued run using the Phase 2 schema, applies the Phase 3 migration, and removes the schema afterward.

```sh
python -m pip install -r requirements.txt
TEST_DATABASE_URL="$DATABASE_URL" python -m unittest backend.tests.test_worker -v
```

| Scenario | Pass criteria |
| --- | --- |
| Competing workers | Concurrent claims yield one owner for a queued run. A worker skips a row locked by a separate transaction and claims another ready run. |
| Interrupted worker | After the original worker saves a checkpoint and stops, the run is unavailable until its lease expires. A replacement receives a new lease and completes from the checkpoint. The expired worker cannot save progress, renew, reschedule, or complete the run. |
| Live worker | A worker blocked in a simulated processing step renews its lease, retains the run, and finishes after the step is released. |
| Retry exhaustion | A retry preserves the checkpoint and delays the next claim. If the worker fails or stops on the final attempt, the run becomes `failed` with `attempts_exhausted`. |
| Unexpected error | A processor error outside the retry policy ends the run with `processor_failed`. |

The [Phase 3 workflow](../.github/workflows/phase-3.yml) runs the suite against PostgreSQL 17. Phase 3 passes when the **PostgreSQL worker acceptance** job reports seven passing tests with zero skips.

### Latest results

| Check | Result |
| --- | --- |
| Source and workflow | Passed Python compilation and verified the workflow’s Python, PostgreSQL service, and test command. |
| Worker and queue with test doubles | Passed checks for checkpoint resume after cancellation, stopping when renewal loses the lease, retry dispatch, and the query sequence that checks the lease after locking the row. |
| Local PostgreSQL | Blocked. The shell denied TCP and Unix socket listeners and could not resolve PyPI to install `psycopg`. |
| GitHub Actions | Pending the first Phase 3 push. |
