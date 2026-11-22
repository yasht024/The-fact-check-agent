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
| GitHub Actions | **Passed** on November 22, 2026, for `main` commit `d3e8f17`. All seven worker acceptance tests passed against PostgreSQL 17 with zero skips. [GitHub Actions run](https://github.com/yasht024/The-fact-check-agent/actions/runs/45052352172) |

## Phase 4. Extract Text From PDFs

Activate the project virtual environment, install `requirements.txt`, and run the standalone extraction tests from the repository root. The suite creates its PDF fixtures in memory and requires no PostgreSQL connection or API keys.

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL -u BRAVE_SEARCH_API_KEY -u GEMINI_API_KEY \
  python -m unittest backend.tests.test_pdf_text -v
```

| Scenario | Pass criteria |
| --- | --- |
| Readable two-page PDF | Return the expected text in document order with page numbers `1` and `2`. A document exactly at the configured page limit succeeds. |
| Mixed text and scanned pages | Return text from the readable pages with their original page numbers. Report the scanned page as skipped. |
| Damaged page among readable pages | Report the damaged page as skipped and preserve text and page numbers on either side. |
| Fully scanned PDF | Report that the PDF has no selectable text and include the skipped page numbers. |
| Password-protected PDF | Report that the password protection must be removed. |
| Malformed PDF | Return a clear invalid-PDF error. |
| PDF above the page limit | Report the configured limit and reject the document before extracting any page text. |
| Parser resource limits | With reduced test budgets, stop the parser subprocess for exceeding its deadline or memory cap. Return the corresponding error and make no result available. |

The parser has a 10-second wall-clock deadline and a 256 MiB address-space cap on Linux. On macOS the 256 MiB budget limits growth above the subprocess's startup mappings. If the memory limit cannot be applied, extraction returns an error before parsing the PDF.

### Latest results

| Check | Result |
| --- | --- |
| PDF text extraction and parser limits | **Passed** on November 22, 2026, with Python 3.12.14 and `pypdf` 7.1.1 on macOS. All nine tests passed with zero skips, including the six document cases, damaged-page recovery, and both parser resource limits. |
| Database-free regression checks | **Passed.** All four upload validation tests passed with zero skips. The worker's configuration check passed with `DATABASE_URL` unset. |
| Other environments | PostgreSQL-backed checks and Linux execution were outside this local verification. |

## Phase 5. Identify Verifiable Claims

Run the simulated Gemini acceptance checks and database-free regressions from the repository root in the project virtual environment.

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL -u BRAVE_SEARCH_API_KEY -u GEMINI_API_KEY -u GEMINI_MODEL \
  python -m unittest backend.tests.test_claims backend.tests.test_pdf_text \
  backend.tests.test_runs.UploadValidationTests -v
```

| Scenario | Pass criteria |
| --- | --- |
| Independent figures | A sentence with two figures produces two assertions with original wording, page number, check target, and qualifiers. |
| Invented excerpt | Reject a response whose excerpt does not occur on the supplied page. |
| Promotional copy | Return an empty list for copy with no verifiable assertions. |
| PDF whitespace and overlap | Accept whitespace variations, retain the source wording, and deduplicate repeated assertions from overlapping sections. |
| Empty input | Return no claims without calling a provider. |
| Invalid, incomplete, or blocked response | Raise `invalid_model_response`, `incomplete_model_response`, or `model_refused` as appropriate and return no partial claim list. |
| Gemini request | Use the configured model, the worker's API key header, one structured JSON candidate, and the documented timeout and output limit. Report missing or rejected credentials and request failures explicitly. |
| Live contract | Run `python -m backend.tests.live_claims` separately from a worker shell with `GEMINI_API_KEY` exported. Use the default model or set `GEMINI_MODEL` to a supported model ID. Check the two-figure fixture and promotional copy. |

### Latest results

| Check | Result |
| --- | --- |
| Simulated extraction | **Passed** on November 23, 2026. All 15 tests passed, including excerpt validation, qualifiers, overlapping sections, and Gemini request and response errors. |
| Regression checks | **Passed.** Nine PDF tests and four upload validation tests passed. The combined run reported 28 passes and zero skips with database and API key variables unset. Python compilation, dependency checks, worker startup without a database, and `git diff --check` also passed. |
| Live model | **Passed** on November 23, 2026, using Gemini `gemini-3.5-flash-lite` with the local worker key loaded. The separate live check returned two independent claims with their original excerpts, page, and qualifiers, and no claims for promotional copy. Phase 5 acceptance is complete. |
| Final configuration and smoke checks | **Passed.** Both Gemini and Tavily keys load from the ignored local `.env`, and settings redact their values. The built frontend and `/healthz` returned HTTP 200 in process. The final offline run passed all 28 tests with zero skips. Tavily search integration is scheduled for Phase 6. |
| Replacement credential check | **Passed** on November 23, 2026, after the saved keys were corrected. The live Gemini contract passed for the two-figure and promotional fixtures. Tavily returned HTTP 200 with a usable search result. |
| Latest live recheck | **Passed.** Gemini's full claim contract passed for the two-figure and promotional fixtures after a retry. A basic Gemini request also returned HTTP 200. Tavily returned HTTP 200 with a usable search result. The first Gemini attempt returned `model_request_failed`, whose underlying cause was not established. |

## Phase 6. Search for sources

Run `.venv/bin/python -m unittest backend.tests.test_search backend.tests.test_worker_search -v`.

- **Passed:** initial and follow-up queries retain scope and period; follow-up removes disputed quantities and can target a recognized or supplied primary publisher.
- **Passed:** Tavily success and deduplication, successful empty results, timeout, provider error, rate limit, quota exhaustion, credential rejection, missing key, and malformed response distinct from empty success. Worker entry-point tests verify returned URLs and distinct empty, timeout, and authentication outcomes.
- **Live worker search passed:** With the local environment loaded, `.venv/bin/python -m backend.app.processing.worker --search-claim backend/tests/fixtures/known_claim.json` returned `completed` and ten candidate URLs including `https://science.nasa.gov/earth/facts`. The managed-shell request failed with `search_request_failed`; the approved network retry passed.

## Phase 7. Retrieve evidence from source pages

Run `.venv/bin/python -m unittest backend.tests.test_retrieval -v`.

- **Passed:** saved synthetic HTML and PDF fixtures yield the relevant 42 TWh passage, final source URL, title, reporting period, retrieval time, and PDF page number; HTML publication metadata is preserved. Script content is excluded.
- **Passed:** unavailable page, declared and streamed oversized responses, private redirect, mixed public/private DNS answers, timeout, redirect limit, unsupported content, and irrelevant content produce explicit failures or no passages. Tests verify IP pinning and TLS hostname preservation.
- **Live retrieval passed, November 23, 2026:** the real fetcher retrieved `https://science.nasa.gov/earth/facts/` and returned five passages including its diameter text. The managed-shell attempt returned `source_unavailable`; the same check passed with approved network access.
- **Limits:** deterministic word matching retrieves candidates for assessment; it does not establish support or a verdict. Period inference recognizes labeled years and year ranges, not every natural-language period. JavaScript rendering and OCR are unavailable. Unknown publication dates remain empty; PDF creation dates are not treated as publication dates.

### Combined Phase 6–7 verification

The Tavily worker integration run passed **51 tests with zero skips**, covering search, worker search entry points, retrieval, PDF extraction, claims, and database-free upload validation. The real worker command returned ten candidates including NASA's Earth facts page. A separate pipeline test confirmed that the default search provider sends Tavily candidates to retrieval, with database storage replaced by test doubles. PostgreSQL pipeline execution was outside this verification.

## Phase 8. Verdicts and corrections

Run `.venv/bin/python -m unittest backend.tests.test_assessment -v` without credentials or PostgreSQL.

| Scenario | Verified behavior |
| --- | --- |
| Matching source and period | Verified, with validated evidence IDs. A newer conflicting-period release does not invalidate a correctly dated historical figure. |
| Source-backed numeric correction | Inaccurate with a source quote, replacement value adjacent to its unit, reporting period, and cited evidence ID. |
| Credible refutation | False with validated source citations. |
| No credible support | False only after successful initial and follow-up searches; search context comes from orchestration. |
| Timeout, inaccessible material source, or unresolved evidence | Incomplete with a null verdict and a separate reason. |
| Unknown evidence ID | Withhold citations and correction; leave the check incomplete. |
| Unsupported correction | Withhold the correction and verdict. Tests cover invented text, wrong value/unit/period, unknown correction ID, missing numeric value, and a year incorrectly proposed as the measured quantity. |
| Provider response | Current adapter uses Gemini structured JSON with bounded output/time; reject blocked, malformed, and incomplete responses. The earlier OpenAI run is historical evidence only. |

**Earlier Phase 8 result (before the Gemini switch), November 23, 2026:** all 13 assessment tests passed against the then-configured OpenAI adapter. No live OpenAI assessment ran. Current Gemini results are recorded separately below. Local validation establishes citation membership and conservative numeric/period checks; semantic source credibility, entailment, and current-release judgments still depend on the model. Units must match the quoted quantity directly; automatic unit conversion is deliberately unsupported.

## Phase 9. Connect the pipeline

Apply all three migrations to the API/worker database. For tests, configure `TEST_DATABASE_URL` to a disposable UTF-8 PostgreSQL database; tests create and remove isolated schemas.

```sh
.venv/bin/python -m unittest discover -s backend/tests -t . -v
```

The `Backend pipeline acceptance` workflow in `.github/workflows/pipeline.yml` runs this command with PostgreSQL 17 and no provider credentials.

| Scenario | Verified behavior |
| --- | --- |
| Upload to saved report | Real API upload, PDF parser, validated simulated Gemini claims, saved HTML passages, simulated assessment, PostgreSQL finding, and authenticated report all work together. |
| One failed check | A forced search timeout leaves that check incomplete while another saved Verified result remains visible; the run completes with gaps. |
| Restart after one finding commits | A replacement skips extraction and the committed finding, completes the pending claim, rejects stale-lease writes, and creates no duplicate claims/checks/evidence. |
| Blocking provider | The full pipeline renews its lease while extraction blocks in a thread; another worker cannot claim the run. |
| Atomic finding writes | A forced invalid evidence timestamp rolls back both evidence and finding writes; retrying the valid transaction is idempotent. |
| PDF lifecycle | Delete the staged PDF with the extraction checkpoint or on terminal parsing failure. Keep it through a retryable extraction failure, then delete after successful retry. |
| Empty and partial PDF | Zero claims completes successfully; unreadable pages and a claim cap become saved coverage gaps. |
| Deadlines and retention | Preserve committed results when closing pending checks; close expired queued runs, remove their PDFs, deny expired reports immediately, and cascade child rows during cleanup. |
| Durable usage limits | Search reservations persist across restart; exhausted budgets yield incomplete checks. Transient assessment failures retry with persisted attempt counts. |
| Material source failure and unknown citation | Neither produces False or an invalid published citation. |
| Unread candidate budget | Follow-up retrieves additional unseen candidates. If candidates remain unread when the retrieval budget ends, withhold a no-support False verdict. |

**Verification:** the complete suite passed **90 tests with zero skips** against temporary PostgreSQL 18.6 before the final unread-candidate guard was added. The final affected-suite result is recorded below. All seven legacy worker and six upload/API tests passed in the full run. Python compilation, dependency checks, and `git diff --check` passed. PostgreSQL 17 hosted execution and a full live-provider run remain unrun; CI configuration has been added, but no push or deployment was made.

**Final affected-suite result:** after adding the unread-candidate guard and strengthening value/unit association checks, all **36** pipeline, assessment, and worker-search tests passed against real PostgreSQL, with zero skips. This includes all **18** pipeline integration cases and all **13** verdict/correction cases. That checkpoint preceded the Gemini migration recorded below.


## Gemini migration — automated results

**November 23, 2026. Passed:** the final full backend discovery suite ran **101 tests with zero skips** against real PostgreSQL 18.6 after switching assessment to Gemini and adding live-discovered publisher-discovery, numeric-passage ranking, correction-format, relative-period, and equivalent-calendar-year regressions. The earlier 95-, 98-, 99-, and 100-test runs also passed before the final Tavily domain-filter regression was added. This includes all verdict, historical-period, citation, correction, incomplete-check, worker search, API, recovery, persistence, cleanup, and usage-budget tests. All providers in this automated suite are simulated; source fixtures and PostgreSQL are real.

Gemini-specific tests verify the shared key/model, `generateContent` endpoint, extraction-compatible schema request, ignored thought parts, missing/invalid configuration, invalid/expired-key detection, rate limits, timeout, refusal/blocking, malformed JSON, and incomplete generation. A stale `OPENAI_API_KEY` alone cannot enable assessment. The initial fixture run failed because HTTPX responses lacked their originating request; the fixed fixture helper and full suite passed.

Primary-publisher tests verify that the initial query can target the recognized publisher and the subsequent weak-evidence query broadens beyond it, while keeping Tavily as the adapter. Period regression tests verify that "latest completed calendar year" is a relative period, while a year explicitly named in a historical assertion remains dated.

## Gemini migration — local database and live results

**Development database preparation:** no `DATABASE_URL` was saved in `.env`; the documented loopback address `127.0.0.1:55432/factcheck` refused connections. Started the available PostgreSQL 18.6 binaries as a local development server with its data under ignored local notes. Applied missing migrations 001, 002, and **003**, then verified `runs`, `uploads`, `claims`, `claim_checks`, and `evidence`. This is the persistent development database, distinct from the isolated temporary databases used for automated tests.

**First live attempt: incomplete, not passed.** The actual upload endpoint accepted a small synthetic Amazon PDF; Gemini extraction, Tavily searches, source retrieval, and PostgreSQL writes ran through a standalone worker. The saved report `cd4ff3df-970a-48be-bdad-b5d96266af21` had two null verdicts with `material_source_unavailable` because SEC/secondary sources were unavailable and Business Wire timed out. No assessment requests were made, and no unsupported verdict or correction was published. The staged PDF was removed. This failure is separate from the automated passes.

**Recovery:** verified that Amazon's own investor-relations and corporate annual-results pages are readable by the actual source fetcher. Added initial primary-publisher targeting and retained a broad follow-up; did not remove material-failure, citation, correction, or historical-period guards. Clarified the synthetic current claim to "latest completed calendar year", excluding trailing-twelve-month sales. The rerun uses real Gemini/Tavily providers with no mocks; its final outcome is recorded below.

The repeatable opt-in command is `.venv/bin/python -m backend.tests.live_pipeline` after loading `.env` into the shell with tracing disabled. It uses real loopback HTTP upload/report routes, a separate worker process, the development database, live model/search calls, and live source retrieval. Synthetic PDF and sanitized report artifacts stay in ignored `local-notes/phase9-live/`; the report access token and provider keys are neither printed nor saved. The helper stops only the API/worker processes it starts. No deployment, publication, frontend work, or later phase is included.


**Additional live attempts, retained separately:**

- `674d1e29-9ea3-4ff1-9aac-aba51439af58`: incomplete historical discovery and a withheld unsupported correction. A separate Gemini diagnostic against the saved current-year passages subsequently returned a valid 716.9 / billion / 2025 proposal; this diagnostic was not counted as a passing end-to-end run.
- `0dedb2ba-a0e7-4ee4-a9b6-eca979a89f9b`: saved **Inaccurate** for the outdated current figure, with value **716.9**, unit **billion**, period **2025**, and the issuer's live 2025 results link. The historical claim remained incomplete because `calendar year 2024` did not literally match `2024`; broad follow-up then encountered inaccessible SEC sources. The complete two-claim acceptance still failed.

The observed calendar-year mismatch was fixed conservatively: `calendar year YYYY`/`year YYYY` normalize to `YYYY`; differing years still fail, and fiscal-year labels are not collapsed into calendar-year labels. A full 100-test run passed after this period fix; the subsequent Tavily domain-filter fix passed the complete 101-test run. All failures remain visible in their original saved reports; no completed/incomplete findings were manually rewritten to manufacture a pass.


**Fourth live attempt:** `8a929b50-9372-420d-b676-7581d7f5c206` saved **Verified** for the historical 2024 figure. Its current-figure check had a transient provider error, then weak evidence, then an inaccessible SEC source on broad follow-up, so that check stayed incomplete. Expected individual verdicts had now appeared in separate reports, but this was still not a passing two-claim run.

**Final provider-contract correction:** primary-publisher `site:` hints are now translated to Tavily's documented [`include_domains` parameter](https://docs.tavily.com/documentation/api-reference/endpoint/search) and removed from the semantic query. Broad follow-up requests omit this restriction. The full suite passed all 101 tests after this fix. Original material-source failures remain intact in their saved reports.

### Final live local Phase 9 acceptance — passed

**November 23, 2026; run `bf02baae-cc9b-487e-877f-d4410a1b9fed`.** The real local API accepted the two-claim PDF with HTTP 202; a separate worker process used Gemini for extraction and assessment, Tavily for discovery, public HTML/PDF retrieval, and the migrated development PostgreSQL database. Authenticated report polling returned HTTP 200 and final state **completed**, with two completed findings and no material-source failures.

| Synthetic assertion | Saved result | Source verification |
| --- | --- | --- |
| Amazon reported full-year net sales of $638.0 billion for calendar year 2024. | **Verified**, with no correction. | Issuer [2024 annual results](https://ir.aboutamazon.com/news-release/news-release-details/2025/Amazon-com-Announces-Fourth-Quarter-Results/) and the comparative figure in the 2025 release. |
| Amazon's full-year net sales for the latest completed calendar year are $638.0 billion. | **Inaccurate**; corrected value **716.9**, unit **billion**, reporting period **2025**. The source quote preserves the dollar currency. | Issuer [2025 annual results](https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Fourth-Quarter-Results/) establishes both the replacement figure and its period. |

The live helper re-fetched every cited source through the actual retrieval implementation and reproduced the saved passages. It confirmed the correction's evidence ID belongs to the current claim, the cited passage contains the replacement amount and period, PostgreSQL holds exactly two findings for the run, and `uploads` holds zero rows for it. Earlier failed attempts remain unchanged in the database and separate ignored report artifacts; none was reclassified as a pass.

**Final automated result:** all **101 tests passed with zero skips** before this successful live run. Compilation, dependency consistency, and whitespace checks passed. Automated provider fixtures and live provider calls are recorded separately above.

**Local scope complete:** development migration 003 is applied, both Gemini stages and Tavily work through the local Phase 9 pipeline, and the complete report passed verification. No current blocker remains for this local check. The helper stopped its API and worker; the development database and saved reports remain available on the documented local port. No keys or report bearer token were printed or saved to artifacts. No deployment, hosted workflow execution, commit/push, Phase 10, or later-phase work was performed.

## Phase 10: upload and report interface

**Date:** November 23, 2026. **Scope:** local Phase 10 only; no deployment or later-phase evaluation.

**References and implementation.** Read `docs/implementationPlan.md`, `docs/conventions.md`, the Proofline design system, and all six desktop/mobile exports under `docs/Design/stitch_fact_checking_web_app/`. Adapted the navy/paper shell, editorial type, upload requirements sidebar, stage progress, filters, badges, and claim cards into React/CSS. Preview switches and placeholder navigation were omitted. Public limits come from `/api/config`; page/text checks remain server-side. Browser session storage holds only the active run credential and filename.

### Automated checks

| Check | Observed result |
| --- | --- |
| Production frontend build | Passed with official standalone Node 24.11.1 and existing dependencies: `node frontend/node_modules/vite/bin/vite.js build frontend`. |
| Frontend API tests | Four passed, zero skips: `node --test frontend/src/api.test.js`. Checked bearer header/no token URL, multipart upload contract, HTTP error status, and HTTP(S)-only citation links. |
| Configuration and upload API regressions | Seven passed, zero skips against local PostgreSQL 18.6: `TEST_DATABASE_URL=… .venv/bin/python -m unittest backend.tests.test_interface_config backend.tests.test_runs -v`. Public limits follow environment overrides and omit provider credentials. Existing upload validation, persistence, token access, and rollback tests pass. The preliminary run without the database variable skipped two database checks; the database-backed rerun supersedes those skips. |
| Compilation and whitespace | Python compilation and `git diff --check` passed. |

### Browser checks

Used native Google Chrome locally at `http://127.0.0.1:8000/`. The separate `backend.tests.interface_preview` service on port 8010 supplied known responses without provider calls. Synthetic fixtures only; no private document was uploaded.

| Check | Observed result |
| --- | --- |
| Keyboard upload | Passed: Tab from the document reached the visible file input; Space opened the native chooser; the PDF path was selected with the keyboard; Tab and Return submitted **Start fact-check**. Focus moved to the progress heading. |
| Desktop layout | Passed: visually inspected upload, progress, and finished-report views in the wide desktop browser (1440 CSS pixels / 2880 screenshot pixels at 100% zoom). Upload form/sidebar, four stages, filters, and claim cards preserve the chosen desktop hierarchy. An additional 1120-pixel device-preview attempt clipped the preview; it is not counted as a passing viewport check. |
| Mobile layout | Passed at a confirmed 390 CSS-pixel responsive viewport. Inspected upload, progress, coverage gaps, verdict filters, and a complete correction/citation card. Cards stack, controls fit, and original/corrected periods remain readable without horizontal clipping. Desktop and mobile screenshots were reviewed in the tool session. |
| Client validation | Passed: an 18-byte `invalid.pdf` without a PDF signature displayed **This file is not a valid PDF** with submission disabled; a 10 MiB + 1 byte PDF displayed **File exceeds 10 MiB**, also disabled. A small real PDF displayed Ready. |
| Refresh while queued | Passed: same tab restored the filename and queued report without another upload. |
| Refresh during active processing | Passed: after worker extraction, refreshing restored **0 of 2 claims checked**, both original claims, and the active verification stage. |
| Real PDF → finished report | Passed through the production upload/report endpoints, PostgreSQL, standalone worker, Gemini, Tavily, and source retrieval. Run `778920e5-efbc-4eca-a8c7-455d60d9c794` reached `completed_with_gaps` with two saved findings. Current-year claim: Inaccurate, correction **716.9 billion**, period **2025**, with an issuer citation. Historical claim: Check incomplete, reason `material_source_unavailable`; no unsupported verdict was substituted. Database inspection confirmed zero staged uploads. |
| Finished-report refresh | Passed: the real report and its findings were restored after refresh in the same tab. |
| Real citation navigation | Passed: keyboard focus reached the source link; Return opened a new tab on Amazon’s “Amazon.com Announces Fourth Quarter Results” release. The page visibly states net sales of **$716.9 billion in 2025**, compared with **$638.0 billion in 2024**. |
| Known verdicts and coverage | Passed: fixture report displayed Verified, Inaccurate, False, and Check incomplete in document order with page numbers, original wording, explanation, correction value/unit/period, and source links. Coverage banner identified skipped page 5 and two omitted claims. Inaccurate filter showed only its matching card. |
| Temporary polling failure | Passed: switched a running fixture from `progress` to `transient` (HTTP 503). Connection banner appeared while all three saved findings and pending claim stayed visible. Restoring `report` recovered automatically to the finished report with gaps. |
| Empty report | Passed: `empty` response displayed **No checkable claims found** and explained that this does not establish accuracy. |
| Unreadable PDF | Passed: `pdf_no_selectable_text` failure displayed **Unable to read PDF text**, OCR/text-PDF guidance, and an action to check another PDF. |
| Unavailable/expired report | Passed: HTTP 404 displayed **Report unavailable**, expiration guidance, and **Check another PDF**. |

**Limitations and unrun checks.** The live historical claim encountered an inaccessible material source and correctly stayed incomplete; this UI acceptance run does not establish model accuracy or replace Phase 9’s separate two-verdict provider evaluation. The 1120-pixel emulation attempt was not accepted as a layout pass; desktop verification used the actual wide window and mobile used the confirmed 390-pixel viewport. Upload HTTP 503 text handling is covered by the API-client test path, but a separate browser submission-error check was not run. Closed-tab/cross-device report recovery is outside the same-tab requirement. Hosted PostgreSQL 17, deployment, public-URL verification, and Phases 11–12 were not run.

**Local outcome.** Phase 10 implemented and locally verified. The non-secret browser report is retained under ignored `local-notes/phase10/browser-report.json`. Access tokens and provider credentials were not recorded in evaluation artifacts. No commit, push, or publication was performed.

The fixture server was stopped after verification. The local API at `http://127.0.0.1:8000/` and normal worker were left running for local use.

## Phase 11: Railway and Vercel deployment

**Date:** November 23, 2026 (Asia/Kolkata). **Status:** deployed; public small-PDF HTTP flow and primary citation verified. Fresh-browser UI acceptance and full-limit upload transport remain pending. Phase 12 has not started.

### Hosting decision and release configuration

The architecture and original Phase 11 roadmap specified Render for API, worker, and PostgreSQL. The user's explicit choice of Railway backend and Vercel frontend superseded that plan. No Render/Railway decision remains open. Deployed the current local source through authenticated Railway 5.75.0 and Vercel 66.6.0 CLIs; did not deploy the older remote Git revision or make a commit/push. No paid plan upgrade was purchased.

| Resource | Actual target/result |
| --- | --- |
| Public React app | **[https://proofline-omega-nine.vercel.app](https://proofline-omega-nine.vercel.app)** |
| Public FastAPI service | **[https://api-production-7966543a.up.railway.app](https://api-production-7966543a.up.railway.app)** |
| Railway project | `proofline`, project `080ee1d1-545f-4e76-aa36-a5c12db43914`, production environment, region `iad`. |
| Railway API release | `4c624011-f185-48f9-9f45-a867f07b4113`; successful, one running replica. Earlier initial release `18b2351b-f2b2-46b8-a105-d3ae87186103` also passed health. |
| Railway worker release | `78256458-2a5b-4f63-b75f-dde60517c149`; successful, one running replica, no public domain. Initial release `3f73ab39-161b-44d6-89a6-6d08313fcb95` was superseded by the source-address fix. |
| PostgreSQL | Railway `ghcr.io/railwayapp-templates/postgres-ssl:18` template, one running replica and persistent 500 MB volume. Major version 18 observed from deployment source; exact minor version was not queried. No public database endpoint was added. |
| Vercel release | `dpl_V8nMYSbHs98ZuDQEqgKTFjjzfrV5`, READY, production alias above. The unique build URL retains default Vercel authentication protection; the public production alias was tested without credentials or bypass tokens. |

The root Dockerfile builds React using Node 24/frozen pnpm, installs Python 3.12 dependencies, and runs as an unprivileged user. FastAPI serves the built assets as a fallback. Vercel's static Build Output API routes forward `/api/*` and `/healthz` to Railway. `RAILWAY_API_ORIGIN` is configured for Vercel Production and Preview. Runtime database references are set on both Railway services; existing Gemini/Tavily credentials were sent privately through CLI stdin to the worker only. Values are absent from Git and evaluation artifacts. Vercel's upload dry-run confirmed `.env`, `.env.local`, `.vercel`, local notes, caches, and the backend are excluded.

Railway's live API rejected the deprecated `railwayConfigFile` setting and the legacy DOCKERFILE builder enum. Replaced the initial config-path assumption with `deploy/*.settings.json` and `scripts/configure_railway.py`, using the introspected service-update API and automatic Dockerfile detection. Both service updates succeeded. Build/start commands, variable locations, and migrations are documented in README.

### Checks and actual results

| Check | Observed result |
| --- | --- |
| React production build | Passed locally and in the Railway/Vercel hosted builds. Both public roots return the Proofline HTML; Vercel JavaScript and CSS assets returned HTTP 200 with their expected content types. |
| Migration release gate | Passed against the fresh Railway database. API pre-deploy logged **3 applied** for 001–003; the worker's gate logged **0 applied** on rerun. Subsequent releases succeeded with the same ledger. Local tests verify concurrent migration serialization, rollback, safe reruns, and refusal of changed or untracked migration history. |
| Backend regression suite | Complete 109-test suite passed after adding address preference/failover. The additional shared-deadline guard also passed in the 14-test retrieval suite; final complete-suite result recorded below. |
| Frontend/deployment-output tests | Seven passed, zero skips: bearer/multipart/error/citation contracts, route ordering/origin validation, and static artifact generation without server environment files. |
| Public API health | HTTP 200 and `{"status":"ok"}` at the Railway `/healthz`. This is process liveness; the real pipeline separately verifies database and worker activity. |
| Public forwarding | Vercel `/healthz` returned HTTP 200/ok; `/api/config` returned HTTP 200 with 10 MiB, 30 pages, and 24 hours. The Vercel root returned HTTP 200 without authentication. |
| Invalid small upload | HTTP 400 through the public Vercel route; no valid run started. |
| Report access/cache | Missing bearer token returned HTTP 404. Authenticated polling through Vercel returned saved progress/findings with `Cache-Control: no-store`. No report token was placed in a URL, printed, or written to an artifact. |
| First public run | `07358bbd-aacc-4ce8-9361-e8f84fb157c2`, synthetic Amazon PDF, accepted HTTP 202. Cloud worker logs show two saved incomplete findings. The local poller timed out before saving the final report; the exact check reasons were not inspected. Not a passing live acceptance run. |
| Second public run | `521cb52c-e2d9-4dac-b47c-24f233e4473c`, small NASA diameter PDF, finished `completed_with_gaps`: null verdict, `material_source_unavailable`, NASA/Universe Today retrieval failures, zero assessment attempts. Report retained under ignored local notes. Not a passing citation acceptance run. |
| Address-handling fix | Two failing tests reproduced selecting IPv6 first and failing without trying another validated answer. Added deterministic IPv4 preference and bounded address fallback under the original deadline. No DNS/private-address, TLS, redirect, response-size, citation, correction, or verdict guard was removed. Added sanitized transport/HTTP-status diagnostics only. |
| Successful public run | **`e121223d-82dc-4e30-a580-de99d3ab0776`**, small NASA PDF uploaded to the Vercel `/api/runs` route, HTTP 202. Cloud PDF parsing, Gemini extraction, Tavily discovery, public-source retrieval, Gemini assessment, PostgreSQL persistence, and authenticated report polling completed. Final state **completed**, one **Verified** finding, page 1, original excerpt preserved, no source failures or coverage gaps. Three cited passages were saved. |
| Actual citation supports finding | Opened the actual [NASA Earth facts citation](https://science.nasa.gov/earth/facts/) through the web tool and separately fetched it with HTTPX: HTTP 200. Its **Size and Distance** section explicitly gives Earth's equatorial diameter as **12,756 kilometers**. This matches the report's claim and saved NASA passage. No screenshot/browser interaction was used. |
| Strict local passage reproduction | The opt-in HTTP helper reached the successful cloud report, then its strict local IP-pinned re-fetch timed out before reproducing all citations. That complete helper invocation remains failed; it was not relabeled as a pass. Independent normal HTTPS and web-tool NASA verification passed as recorded above. |
| Large-upload boundary | **Unverified.** 10 MiB+1 byte requests timed out through Vercel (read/write) and directly against Railway (45-second timeout after sending the multipart body). No public 413 was observed. The same input returned HTTP 413 in-process in 0.036 seconds. Cause of hosted transport timeout is not established; no upload limit was silently lowered. The exact-limit public test was not reached. |
| Fresh browser, hosted keyboard/layout, refresh | **Not run.** Computer use was stopped immediately at the user's request. The user was asked to open the public app in a fresh session, upload a small PDF, and open a citation; no result has been supplied. Phase 10 local browser results do not count as hosted browser verification. |

**Remaining user action.** Complete the fresh-browser public upload/report/citation check while computer use remains disallowed, and report the observed result. Keep hosting resources/provider quota available for review. No current sign-in, credential, or billing-upgrade action blocks the deployed small-PDF HTTP flow. Large-upload timeout investigation remains open. Full Phase 11 acceptance is not marked complete; no Phase 12 demo/submission work was performed.

**Final local regression result:** all **110 backend tests passed with zero skips** against isolated local PostgreSQL schemas after the shared-deadline regression was added. All **seven frontend/deployment-output tests** passed. Python compilation and `git diff --check` passed. The hosted API and worker both run the source-address fix; no further runtime code changed after the successful public report.

## GitHub publication and Vercel Git connection

**November 23, 2026.** At the start of this publication task, local `HEAD` and remote `origin/main` both pointed to `d3e8f17` (Phase 3); application Phases 4–11 had not been committed or pushed. The user explicitly requested publishing the current project, making the README understandable for interview review/local development, and connecting Vercel to GitHub where appropriate.

The README was rewritten around product usage, report semantics, architecture/trade-offs, a consistent local setup, tests, deployment ownership, and known limitations. Generated `output/` documents stay local alongside environment files, CLI state, build artifacts, and local notes. The private repository's visibility is preserved. Added frontend build/API-contract/deployment-output checks to the application CI workflow; backend CI uses isolated PostgreSQL 17 without provider keys. Publication and Git-connection verification results are appended below after they are observed.

**Publication result:** pushed application/documentation commit [`b97d5306bba277b5e3cc282186bf8483b896dda8`](https://github.com/yasht024/The-fact-check-agent/commit/b97d5306bba277b5e3cc282186bf8483b896dda8) to `main`. GitHub attributes the commit to `yasht024` using the owner's noreply identity. Staged-file checks found no environment/CLI/generated-output paths and no matches for known local credentials. The README's local links, React build, staged whitespace check, and local tests passed. PDF fixture bytes are marked binary so their valid xref padding is preserved.

**Hosted CI result:** all three workflows passed for that commit: [Application acceptance](https://github.com/yasht024/The-fact-check-agent/actions/runs/45072968405), [Phase 2 acceptance](https://github.com/yasht024/The-fact-check-agent/actions/runs/45072968342), and [Phase 3 worker acceptance](https://github.com/yasht024/The-fact-check-agent/actions/runs/45072968337). The application workflow's log confirms **110 backend tests, zero skips, on PostgreSQL 17**, four frontend API tests, three deployment-output tests, and a successful React build on Node 24. No live provider credentials are needed by CI.

**Vercel Git connection:** `vercel git connect` succeeded for `yasht024/The-fact-check-agent`. Project inspection confirmed provider `github`, production branch `main`, repository-root build context, framework Other, and Node `24.x`. `RAILWAY_API_ORIGIN` remains configured in Production/Preview. Native Vercel Git integration handles future frontend builds; Railway source/deployment settings were not connected to GitHub or changed by this task. Preview frontends share the existing API, as documented. Repository visibility remains **private**; the owner must grant an interviewer repository access separately if needed. The GitHub description and homepage now identify the project and link to the live app.
