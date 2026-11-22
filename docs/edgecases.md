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

Run the corresponding [Phase 2 acceptance checks](evals.md#phase-2-accept-and-store-a-pdf) or [Phase 3 acceptance checks](evals.md#phase-3-pick-up-and-recover-jobs).

## Discovered During Build

- Multipart boundaries and headers count toward the request size. The request allows up to 64 KiB of multipart overhead. The exact byte limit still applies to the PDF part, so a PDF at the limit is eligible for acceptance. [Phase 2 acceptance checks](evals.md#phase-2-accept-and-store-a-pdf)
