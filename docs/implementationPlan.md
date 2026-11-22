# Fact-Checking Web App Implementation Plan

This roadmap builds the app described in the [problem statement](problem%20statement.md) and [architecture](architecture.md). Each phase ends with a check that can be run before the next piece is connected. Use small PDFs, saved source pages, and simulated provider responses for repeatable tests. Verify the live providers separately and repeat the full workflow on the deployed app before submission.

## Phase 1. Set Up the Project

- Create the React frontend, FastAPI service, and Python worker entry point. Add the initial `requirements.txt` and document how to start each part locally in `README.md`.
- Set up environment-based configuration for the database, provider credentials, and processing limits. Serve the built frontend from FastAPI and add `GET /healthz`.

**Done when** the frontend opens from the web service and the health endpoint responds in a clean local setup. This check requires no provider credentials.

## Phase 2. Accept and Store a PDF

- Add PostgreSQL migrations for `runs` and `uploads`. Implement `POST /api/runs` with a configurable byte limit and PDF signature check. Save the queued run and temporary PDF in the same transaction.
- Implement `GET /api/runs/{run_id}` with a per-run access token. Store only the token hash and return the current run state and stage.

**Done when** an API test uploads a small PDF, receives `202 Accepted`, and retrieves its `queued` state. An oversized upload, a file without a PDF signature, and a report request with the wrong token are rejected. A failed database write leaves neither a queued run nor an orphaned upload.

## Phase 3. Pick Up and Recover Jobs

- Implement the PostgreSQL queue reader and worker lease. Add lease renewal and require the current lease for progress or result writes.
- Use a test processor that records a checkpoint. Add bounded retries for recoverable failures and a terminal error for jobs that exhaust their attempts.

**Done when** two workers compete for one job and only the worker holding its lease can save results. Stop that worker after a checkpoint and verify that a replacement resumes after lease expiry. The original worker’s stale writes are rejected.

## Phase 4. Extract Text From PDFs

- Use `pypdf` to read the file into page-numbered text. Enforce the page limit before extracting claims and bound the parser’s runtime and memory use.
- Record unreadable pages. Return clear errors for malformed, password-protected, or image-only PDFs. Keep readable text from mixed PDFs and report skipped pages.

**Done when** a two-page text PDF yields the expected text and page numbers. Separate fixtures cover a mixed PDF with a scanned page, an entirely scanned PDF, an encrypted file, a malformed file, and a file above the page limit. This phase runs without a database or model API.

## Phase 5. Identify Verifiable Claims

- Use structured model output to turn page text into separate factual assertions. Keep the verbatim excerpt, page, fact to check, and any qualifiers such as units, geography, or reporting period.
- Validate excerpts against the extracted page text, account for PDF whitespace, and deduplicate overlap between text sections. Handle empty input, invalid model output, and incomplete responses.

**Done when** a fixed page and simulated response produce the expected structured claims. A sentence containing two figures yields two checks, invented excerpts are rejected, and a page with only promotional copy yields an empty list. Run a small live model check with the same input to verify the response contract.

## Phase 6. Search for Sources

- Build search queries from the claim and its qualifiers. Prepare a follow-up query around the underlying fact, with a likely primary publisher where identifiable, for cases where the first pass gives weak evidence.
- Connect the Brave Search API. Return candidate source URLs and distinguish a successful empty result from a timeout, rate limit, or credential error.

**Done when** simulated responses exercise each search outcome and a live request for a known public report returns candidate source URLs. This phase uses a prewritten claim and runs independently of PDF extraction.

## Phase 7. Retrieve Evidence From Source Pages

- Fetch candidate HTML pages and PDFs with limits on response size, redirects, and time. Enforce public-address checks at connection time and on redirects.
- Extract passages relevant to the claim. Keep each source’s final URL, title, retrieval time, publication date or reporting period where available, and the supporting text. Prefer original publications and record when a material source could not be read.

**Done when** saved HTML and PDF fixtures yield the expected evidence and source metadata. Test that an unavailable page, a redirect to a private address, and an oversized response are handled without creating evidence. Retrieve one public source end to end to confirm the live path.

## Phase 8. Apply Verdicts and Validate Corrections

- Compare the claim with the retrieved excerpts using structured model output. Check that every cited evidence ID belongs to the claim and build links from stored URLs. Check the value, unit, and period of numeric corrections against the cited excerpt where they can be parsed reliably.
- Apply the three verdicts in the architecture. Distinguish a completed search with no credible support from an interrupted search, inaccessible material evidence, or an unresolved source conflict. Leave the verdict empty for incomplete checks.

**Done when** fixed claims, evidence, and simulated assessments pass the following cases without live providers.

| Case | Expected result |
| --- | --- |
| A source supports the figure and its stated historical period. | **Verified** with the source link. |
| A newer release supersedes a figure presented as current. | **Inaccurate** with the replacement figure, reporting period, and source. |
| Credible evidence refutes the premise. | **False** with the reason and source. |
| A completed search and follow-up find no credible support. | **False** with an explanation of the search outcome. |
| A search times out or relevant sources leave a conflict unresolved. | Incomplete check with an explanation and an empty verdict. |
| An assessment provides an unknown evidence ID or unsupported correction. | The invalid link or correction is withheld. |

## Phase 9. Connect the Worker and Save Results

- Connect PDF parsing, claim extraction, initial and follow-up searches, evidence retrieval, and verdict assignment. Migrate the `claims`, `claim_checks`, and `evidence` tables, then save each result and its evidence together.
- Delete the staged PDF after claim extraction commits or ends in a terminal failure. Update progress from saved checks, enforce the processing deadline and usage limits, and clean up expired reports. Log processing stages and provider errors without recording document contents, queries, or access tokens.

**Done when** an API test uploads a known PDF and receives a saved report with simulated providers. Force one claim to fail and confirm that completed claims remain visible. Restart the worker mid-run to check that saved verdicts survive without duplicate rows. Verify upload cleanup and report expiry using short test deadlines.

## Phase 10. Build the Upload and Report Interface

- Prepare desktop and mobile designs in Google Stitch using the [design brief](problem%20statement.md#google-stitch-design-brief). Design work can start ahead of this phase. Save the chosen screen references in `docs/design/` and adapt them into the React interface in this phase.
- Connect the React upload screen to the API. Display input limits, keep the report token in session storage, and poll until the run completes. Preserve the last results during temporary polling errors.
- Show the original claim, page number, verdict, short explanation, correction when available, and source links. Show distinct states for empty reports, incomplete checks, and unreadable PDFs.

**Done when** a browser can upload a test PDF, reload during processing, and reach the finished report in the same tab. Use known API responses to check each error and report state independently of live providers. Compare the upload, progress, and report views with the chosen Stitch designs at desktop and mobile widths, verify keyboard use of the upload form, and confirm that source links open the cited pages.

## Phase 11. Deploy and Check the Live App

- Deploy the FastAPI web service, worker, and PostgreSQL to Render from the GitHub repository. Apply migrations, set production credentials, and confirm that the worker can reach the database and both providers.
- Check service health, worker activity, and available provider quota. Run one full check through the public URL with live search and model calls.

**Done when** a fresh browser session can open the public URL, upload a PDF without an account, and receive a finished report with at least one source link. Confirm that the cited page supports the report and that the app remains reachable for the review window.

## Phase 12. Evaluate and Prepare the Submission

- Create a test PDF with a supported claim, a correctly dated historical figure, an outdated statistic presented as current, and a fabricated claim. Record the expected verdicts and the official sources for the supported figures before running it through the deployed app.
- Review the output claim by claim. Check that the app flags the intended traps, provides supported corrections, and preserves valid historical statements. Verify that a simulated provider outage leaves a check incomplete.
- Finish `README.md` with setup, configuration, usage, deployment, and known limitations. Record a 30-second demo showing a PDF upload, a flagged claim, and its supporting source. Put the live app, GitHub repository, and demo links in one submission document or email.

**Done when** the test PDF passes on the public deployment and all three submission links open with the intended reviewer access.
