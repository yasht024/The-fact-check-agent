# Fact-Checking Web App

## Overview

Marketing materials often cite statistics, dates, and figures to support a message. A figure that was accurate when a document was drafted can become outdated before publication. Other claims may contain errors or lack a reliable source. Checking each claim by hand takes time and makes it easy to miss a figure buried in a longer document.

## Objective

Build a deployed web app that accepts a PDF, extracts factual claims, and checks them using a live web search. The app should produce a claim-by-claim report that explains each verdict and provides a source-backed correction when one is available.

## Target Users

- Marketing and content teams checking claims before publishing a document.
- Editors and reviewers who need to trace statistics and other factual statements back to their sources.

## Scope of Work

### 1. PDF Upload and Claim Extraction

- Provide a simple interface where users can upload a PDF and start a fact check.
- Identify statements that can be checked against external evidence, including percentages, counts, dates, and financial or technical figures.
- Preserve the claim as it appears in the document and show its page number where available. Keep details such as the unit, location, population, or reporting period when they affect how the claim should be checked.
- Show a clear message if the PDF cannot be read or if it contains no claims the app can check.

### 2. Live Web Verification

- Run a live web search for each extracted claim. Use details from the document to find sources that address the specific figure, event, or statement.
- Prefer primary sources where available, such as official statistics, regulatory filings, research publications, or the organization that published the original figure.
- Check that the evidence refers to the same metric, geography, population, and reporting period where relevant. A historical claim should be checked against evidence for the period it names. A figure presented as current should be checked against the latest applicable release.
- Use the source page or document to support the verdict. If a search fails or is interrupted, show that the check could not be completed. Assign a verdict only after the search completes.

### 3. Fact-Check Report

Show the following for each checked claim.

- The original claim and its page number where available.
- A **Verified**, **Inaccurate**, or **False** verdict with a short explanation.
- The corrected figure or fact when reliable evidence supports one, along with the relevant date or reporting period.
- Direct links to the sources used to support the verdict or correction.

| Verdict | Meaning |
| --- | --- |
| **Verified** | Credible evidence supports the claim as written, including any date or scope it specifies. |
| **Inaccurate** | The claim refers to a real metric or event, but a stated detail is wrong or a figure presented as current is outdated. Include the correction when the evidence supports one. |
| **False** | Credible evidence refutes the premise of the claim, or a completed search finds no credible evidence supporting it. State whether the claim was contradicted or whether the search found no support. |

### 4. Interface and Deployment

- Design the frontend in Google Stitch and implement the chosen screens in React.
- Make the upload, progress, and results easy to find. Keep the report readable when a document contains several claims.
- Deploy the app to a hosting platform such as Streamlit Community Cloud, Vercel, or Render. Reviewers must be able to open the live URL and run the full workflow with their own PDFs.

### Google Stitch design brief

Design a responsive web app for marketing and editorial teams who need to fact-check claims in a PDF before publication. Show the flow from selecting a single PDF to reviewing the findings and opening their sources. Use synthetic sample data and design for desktop and mobile.

- Give the **upload screen** a labeled file picker, a submit button, and a note that PDFs need selectable text. Show the default limits of 10 MiB and 30 pages per PDF and 24 hours of report access after completion. Include invalid-file and oversized-file errors.
- Show the current stage and claim progress in the **progress view** when available. Keep any available findings visible if the connection drops temporarily.
- List **report findings** in document order with the original claim, page number, verdict or check status, explanation, correction and reporting period when available, and source links. Use text labels for Verified, Inaccurate, False, and Check incomplete.
- Show clear **empty and error states** for PDFs with no checkable claims, skipped pages, unreadable PDFs, and unavailable reports.

## Evaluation and Success Criteria

Reviewers will upload a test PDF containing deliberately false claims and outdated statistics. The app succeeds if it can flag those claims and provide the correct facts wherever reliable sources establish them.

- Extract the specific statements the reviewers expect the app to check.
- Verify claims that match the evidence, including correctly dated historical figures.
- Flag outdated statistics presented as current and show the supported replacement figure and its reporting period.
- Flag fabricated or unsupported claims and explain what the search found.
- Let reviewers open the cited sources and confirm that they support the findings.
- Complete the upload-to-report workflow on the deployed app using a reviewer’s PDF.

## Expected Deliverables

Submit all three links in a single document or email.

1. **Deployed app.** A live URL where reviewers can upload a PDF and view the fact-check report.
2. **GitHub repository.** Clean source code, a `requirements.txt` file, and a `README.md` covering setup, usage, how verification works, and any known limitations.
3. **Demo video.** A 30-second screen recording showing a PDF upload and the resulting report, including a flagged claim and its supporting evidence. The recording should let reviewers see the workflow if API credits run out.
