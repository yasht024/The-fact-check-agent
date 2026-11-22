# Fact-Checking Web App Conventions

Use this file alongside the [problem statement](problem%20statement.md), [architecture](architecture.md), and [implementation plan](implementationPlan.md). Follow the current phase and add folders or modules as their responsibilities appear.

## Repository Layout

| Path | Contents |
| --- | --- |
| `frontend/src/` | React upload form, progress view, report components, and a shared API client. |
| `docs/design/` | Stitch project links or screen exports and any exported `DESIGN.md`, added when the designs are ready. |
| `backend/app/` | FastAPI entry point, API schemas, configuration, and data access shared by the web service and worker. |
| `backend/app/processing/` | Worker and processing logic for PDF parsing, claim extraction, source retrieval, and evidence assessment. |
| `backend/migrations/` | Ordered PostgreSQL schema changes. |
| `backend/tests/` | Backend tests and small PDF, source, and provider-response fixtures. |
| `requirements.txt` and `README.md` | Shared Python dependencies, setup, usage, and deployment instructions. |

Use React for the interface, FastAPI for HTTP requests, PostgreSQL for the queue and reports, and `pypdf` for text extraction. Keep Brave Search and OpenAI calls in the worker-side code. Put each provider's request and response handling in one place so tests can replace the provider with fixed responses.

## Naming and Data

- Use `snake_case` for Python modules, functions, database columns, and JSON fields. Use `PascalCase` for Python classes and React components and `camelCase` for local frontend variables and functions.
- Use names that reflect the data, such as `run_id`, `claim_id`, `page_number`, `reporting_period`, and `retrieved_at`. Keep one meaning per term. A *run* is one PDF submission, a *claim* is one factual assertion, and *evidence* is a passage or table entry from a retrieved source.
- Use one-based page numbers in the API and report. Keep a claim's original wording separate from the normalized text used to search. Keep the reported value, unit, and period together when displaying a correction.
- Store operational timestamps in UTC and serialize them in ISO 8601 format. Keep the source's reporting period separate from its publication and retrieval dates. Use `null` for unknown or unavailable fields.

## API and Worker Behavior

- Use the routes defined in the architecture. `POST /api/runs` returns `202` after the run and PDF have been stored together. `GET /api/runs/{run_id}` returns progress and saved findings for the run's bearer token. Keep `/healthz` independent of search and model credentials.
- Give API errors a stable `code` and a short, user-facing `message`, for example `{"error":{"code":"invalid_pdf","message":"Upload a valid PDF."}}`. Use `400` for invalid upload input, `413` for a file above the byte limit, `404` for an unavailable report, and `429` for rate limits. Give background failures a reason on the run or check that failed.
- Use `queued`, `running`, `completed`, `completed_with_gaps`, and `failed` for run states. Keep job state separate from claim verdict. A readable PDF with zero checkable claims is an empty result, and a run with unreadable pages or incomplete checks shows its coverage gaps.
- Keep PDF parsing and provider calls outside the HTTP request path and database transactions. Use short transactions for related writes, including saving a check with its evidence. Require the current worker lease for writes and resume from saved checkpoints. Retry transient provider failures within the run's limits and preserve findings already saved.

## Evidence and Verdicts

- Validate model-extracted excerpts against the specified PDF page. Split independent assertions into individual checks and keep the qualifiers needed to verify them, including geography, population, units, and time period.
- Run a live search per claim. Use search results to find source material. Base findings on the retrieved passage or table, preferably from the original publisher. Match the claim to the source's definition and scope. Compare historical claims with the named period and current claims with the latest applicable release.
- Link to the saved source URL for each piece of cited evidence. Validate evidence IDs against the passages provided for that claim. Include a correction only when the evidence supports it, and include the period and unit where relevant.

| API verdict | Report label | Use when |
| --- | --- | --- |
| `"verified"` | **Verified** | The retrieved evidence supports the claim as written. |
| `"inaccurate"` | **Inaccurate** | A detail about a real metric or event is wrong, or a figure presented as current is outdated. |
| `"false"` | **False** | Credible evidence refutes the premise, or a completed search and follow-up find no credible support. Explain which condition applies. |
| `null` | **Check incomplete** | A failed search, inaccessible material source, unresolved conflict, or exhausted processing budget prevents a verdict. Keep the reason separate from the verdict. |

## Interface and Configuration

- Use the chosen Google Stitch screens as the visual reference. Adapt any exported markup and styles into React components. Drive upload status, progress, and verdicts from API responses.
- Display claims in document order. Show the original excerpt, page number, verdict or check status, explanation, and any correction and source links. Keep completed findings visible during a temporary polling error. Use text for state labels so they are legible without color.
- Validate upload limits server-side and display the configured limits on the form. Start with the architecture defaults of 10 MiB and 30 pages per PDF and 24 hours of report access after completion. Explain that the first release supports PDFs with selectable text and show any unreadable pages in the report.
- Keep credentials in server-side environment variables. Require provider credentials when the worker reaches those stages. Keep report access tokens in browser session storage, send them in the authorization header, and store only their hashes in PostgreSQL.
- Log run IDs, stages, durations, and error codes. Exclude PDF contents, search queries, access tokens, and credentials. Render retrieved and uploaded text as plain text. Fetch source URLs only over public HTTP(S), with connection and redirect checks and bounded response sizes and times.
- Remove the staged PDF after claim extraction commits or extraction ends in a terminal failure. Close abandoned runs and delete expired reports through the worker's cleanup pass.

## Testing and Phase Review

- Run the current phase's acceptance check before moving on. Use small, synthetic PDFs and fixed provider responses for repeatable tests. Run live provider checks separately and reserve the full public-URL test for deployment.
- When implementing the Stitch design, compare the running upload, progress, and report views with the chosen designs at desktop and mobile widths. Check that form controls have labels and work with the keyboard.
- Verify claim handling at the boundary between a completed search with no credible support and an incomplete search. Include a correctly dated historical figure and a current claim with an outdated value when testing corrections.
- Review changed files, run the relevant regression checks, and record observed edge cases and durable decisions in their project documents. Add libraries or new patterns when the active phase needs them and record decisions that affect later phases.
