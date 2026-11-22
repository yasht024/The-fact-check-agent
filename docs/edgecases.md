# Edge Cases

## Anticipated

| Phase | Trigger | Expected handling |
| --- | --- | --- |
| 2 | The request omits the file, uses a different field name, or sends more than one file. | Return `400 invalid_pdf` before contacting PostgreSQL. |
| 2 | A file has a `.pdf` name and PDF content type but lacks the `%PDF-` signature. | Return `400 invalid_pdf`. Check the uploaded bytes. |
| 2 | A PDF is exactly `MAX_UPLOAD_BYTES` or one byte larger. | Accept the file at the limit. Return `413 upload_too_large` one byte above it. |
| 2 | An oversized request arrives in chunks without a `Content-Length` header. | Enforce the body limit while reading the stream and return `413 upload_too_large`. |
| 2 | A request uses no token, a wrong token, or the token for a different run. | Return the same `404 report_unavailable` response. |
| 2 | PostgreSQL rejects the upload insert after the run insert. | Return `503 database_unavailable`. The failed run ID has no row in either table. |
| 3 | Two workers claim the same queued run. | Only one receives a lease. A worker skips rows locked by another transaction. |
| 3 | A worker resumes after the lease expires or a replacement takes ownership. | Reject its checkpoint, completion, and retry writes. The replacement continues from the saved stage. |
| 3 | An interruption consumes the final attempt. | When its lease expires, record `attempts_exhausted` and close the run. |
| 5 | Gemini blocks a prompt or candidate, truncates generation, or returns malformed output. | Raise the corresponding extraction error and discard the document's candidate claims. |

Run the corresponding [Phase 2 acceptance checks](evals.md#phase-2-accept-and-store-a-pdf), [Phase 3 acceptance checks](evals.md#phase-3-pick-up-and-recover-jobs), [Phase 4 acceptance checks](evals.md#phase-4-extract-text-from-pdfs), or [Phase 5 acceptance checks](evals.md#phase-5-identify-verifiable-claims).

## Discovered During Build

- Multipart boundaries and headers count toward the request size. The request allows up to 64 KiB of multipart overhead. The exact byte limit still applies to the PDF part, so a PDF at the limit is eligible for acceptance. [Phase 2 acceptance checks](evals.md#phase-2-accept-and-store-a-pdf)
- A PDF can have a readable page tree and a damaged content stream on a single page. When neighboring pages yield text, keep their original page numbers and report the damaged page as skipped. [Phase 4 acceptance checks](evals.md#phase-4-extract-text-from-pdfs)

## Phases 6–7 observations

- A successful Brave response may omit the web section when no web results exist; a valid query envelope with no results is empty success. Missing or malformed response envelopes are failures.
- All DNS answers must be public. Reject mixed public/private answers, loopback, private ranges, link-local metadata addresses, and IPv4-mapped loopback. Resolve each redirect independently and connect only to the validated IP to avoid a second DNS lookup at connection time.
- Do not treat a search title, snippet, or an inaccessible source as supporting text. Retrieval failures carry stable codes; later orchestration must retain material failures instead of treating them as evidence of absence.
- Preserve source-derived periods separately from claim periods. Copy only explicit period metadata or labeled years from passages; publication and retrieval dates do not establish a reporting period. PDF metadata creation timestamps do not establish publication dates.
- HTML extraction cannot render JavaScript; PDF extraction cannot OCR scanned pages. Encoded bodies are rejected rather than decompressed without a bound.

## Phases 8–9 observations

- Citation IDs are claim-local. Unknown IDs produce no citations or correction and leave the verdict null. The report reader builds source links only from stored, validated evidence.
- A correction must quote retrieved text. Its numeric value must occur next to its named unit, preventing a reporting year elsewhere in the quote from masquerading as the replacement quantity. Unit conversions and complex numeric formats fail conservatively rather than inventing a conversion.
- Newer figures do not refute correctly dated historical claims. A historical correction must use the claim's period even if the assessment also cites a newer source.
- Empty successful searches differ from failed searches. Initial and follow-up must both complete before no-support False is possible. Actual material retrieval failures, unresolved conflicts, and unexamined candidates after the source budget expires prevent that verdict.
- Reserve search and assessment attempts before dispatch. A crash may consume an attempt without producing evidence, but cannot reset the provider budget. Restart skips saved findings; pending claims may repeat provider work within their remaining budget.
- Offloaded synchronous calls may finish after cancellation, but only the pipeline coroutine can write results, and every write checks the current lease. The live worker renews during blocking work.
- Expiry is checked by the report API as well as cleanup. Cleanup skips live leases and uses cascading foreign keys for report children. Provider failures and cleanup preserve findings already committed.


## Gemini migration and local live verification

- Gemini extraction and assessment share the key/model and JSON request pattern. Ignore thought parts; incomplete, blocked, malformed, and failed generations cannot publish verdicts. A legacy OpenAI key does not satisfy Gemini configuration.
- A formatter mismatch can invalidate an otherwise grounded numeric correction. Tell the model to use a bare decimal value, an exact contiguous source quote, and the unit printed next to that value; keep local validation authoritative.
- Relevant years and amounts should contribute to passage ranking alongside metric terms, otherwise generic navigation can outrank the actual reported figure. The verbatim claim excerpt helps recover quantities omitted from the normalized check target.
- "Latest completed calendar year" is relative; "calendar year 2024" is historical. Normalize the equivalent historical labels "calendar year 2024"/"year 2024"/"2024" without conflating fiscal years or permitting a newer year to refute a historical figure.
- Public search URLs can still be blocked or unavailable to the fetcher. Prefer an explicitly recognized original publisher on the first search and broaden the weak-evidence follow-up. Keep inaccessible material sources recorded and incomplete, rather than promoting snippets to evidence.
