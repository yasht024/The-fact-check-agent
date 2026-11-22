import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { citationUrl, getReport, request, uploadPdf } from './api';
import './styles.css';

const storageKey = 'proofline-report';
const terminal = new Set(['completed', 'completed_with_gaps', 'failed']);
const labels = { verified: 'Verified', inaccurate: 'Inaccurate', false: 'False', incomplete: 'Check incomplete' };
const stages = ['Extract selectable text', 'Identify checkable claims', 'Verify against primary sources', 'Assemble final report'];
const failureMessages = {
  pdf_password_protected: 'This PDF is password-protected. Upload an unlocked PDF with selectable text.',
  pdf_no_selectable_text: 'This PDF has no selectable text. Export a text-based PDF or run OCR before uploading.',
  invalid_pdf: 'This PDF could not be read. Export a new PDF and try again.',
  pdf_page_limit_exceeded: 'This PDF exceeds the page limit. Split it into smaller documents.',
  processing_deadline_exceeded: 'The processing time limit was reached. Saved findings remain available below.',
};
function restore() {
  try { const data = JSON.parse(sessionStorage.getItem(storageKey)); return data?.run_id && data?.access_token ? data : null; } catch { return null; }
}
function Badge({ item }) {
  const kind = item.status === 'pending' ? 'pending' : item.verdict || 'incomplete';
  return <span className={`badge ${kind}`}>{kind === 'pending' ? 'Awaiting check' : labels[kind]}</span>;
}
function Finding({ item }) {
  const claim = item.claim;
  const sources = item.evidence || [];
  return <article className="finding card">
    <header><span className="mono">Page {claim.page_number} · Claim {item.sequence}</span><Badge item={item} /></header>
    <blockquote>“{claim.excerpt}”</blockquote>
    {claim.qualifiers?.reporting_period && <div className="mono muted">Claim reporting period: {claim.qualifiers.reporting_period}</div>}
    {item.correction && <div className="correction"><strong>Suggested correction</strong><p>{item.correction.text}</p><div className="mono">{[item.correction.value, item.correction.unit].filter(Boolean).join(' ')}{item.correction.reporting_period && ` · Reporting period: ${item.correction.reporting_period}`}</div></div>}
    <p>{item.explanation || (item.status === 'pending' ? 'This claim is waiting to be checked.' : 'A verdict could not be reached.')}</p>
    {item.reason && <p className="muted">Check reason: {item.reason.replaceAll('_', ' ')}</p>}
    <footer><strong>Sources</strong>{sources.length ? sources.map(source => {
      const url = citationUrl(source.source_url);
      return url && <a key={source.evidence_id} href={url} target="_blank" rel="noopener noreferrer">{source.title || new URL(url).hostname} ↗<span className="sr-only"> (opens in a new tab)</span></a>;
    }) : <span className="muted">{item.status === 'pending' ? 'Sources will appear after checking.' : 'No validated citations available.'}</span>}</footer>
  </article>;
}
function App() {
  const [session, setSession] = useState(restore);
  const [report, setReport] = useState(null);
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState('');
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('all');
  const [retry, setRetry] = useState(0);
  const selectionVersion = useRef(0);
  const heading = useRef(null);
  const done = report && terminal.has(report.state);
  const stageIndex = done ? 4 : report?.stage === 'checking' ? 2 : 0;
  useEffect(() => { request('/api/config').then(setConfig).catch(() => setConfigError('Could not load upload limits.')); }, [retry]);
  useEffect(() => {
    if (!session) return;
    let stopped = false, timer;
    const controller = new AbortController();
    async function poll() {
      try {
        const data = await getReport(session, controller.signal);
        if (stopped) return;
        setReport(data); setPollError(''); setUnavailable(false);
        if (terminal.has(data.state)) return;
      } catch (err) {
        if (stopped) return;
        if (err.status === 404) { setUnavailable(true); setPollError('This report is unavailable or has expired.'); return; }
        setPollError('Temporary connection problem. Reconnecting automatically; saved findings remain visible.');
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [session, retry]);
  useEffect(() => { if (session) heading.current?.focus(); }, [session, done]);
  async function choose(candidate) {
    const version = ++selectionVersion.current;
    setFile(null); setError('');
    if (!candidate || !config) return;
    if (!candidate.name.toLowerCase().endsWith('.pdf')) { setError('Only PDF files are supported. Export your document as a PDF.'); return; }
    if (candidate.size > config.max_upload_bytes) { setError(`File exceeds ${sizeLimit} MiB. Compress or split the PDF.`); return; }
    try {
      const signature = await candidate.slice(0, 5).text();
      if (version !== selectionVersion.current) return;
      if (signature !== '%PDF-') { setError('This file is not a valid PDF. Choose a PDF with selectable text.'); return; }
      setFile(candidate);
    } catch { setError('Could not read this file. Choose it again.'); }
  }
  async function submit(event) {
    event.preventDefault(); if (!file || busy) return;
    setBusy(true); setError('');
    try {
      // Verify session storage before creating a run that could otherwise be lost on refresh.
      sessionStorage.setItem(storageKey, 'null');
      const data = await uploadPdf(file);
      const next = { ...data, filename: file.name };
      sessionStorage.setItem(storageKey, JSON.stringify(next));
      setReport(null); setSession(next); setFilter('all');
    } catch (err) { setError(err.message || 'Upload failed. Please try again.'); }
    finally { setBusy(false); }
  }
  function reset() {
    sessionStorage.removeItem(storageKey); setSession(null); setReport(null); setFile(null); setError(''); setPollError(''); setUnavailable(false); setFilter('all');
  }
  const sizeLimit = config ? Number((config.max_upload_bytes / 1048576).toFixed(2)) : '…';
  const claims = report?.claims || [];
  const visible = claims.filter(item => filter === 'all' || (item.status !== 'pending' && (item.verdict || 'incomplete') === filter));
  return <>
    <a className="skip" href="#main">Skip to content</a>
    <header className="shell"><div className="shell-inner"><a className="brand" href="/" aria-label="Proofline home"><span aria-hidden="true">✓</span>Proofline</a><ol className="workflow" aria-label="Fact-check workflow">{['Upload PDF', 'Progress', 'Report'].map((text, i) => <li key={text} aria-current={i === (!session ? 0 : done ? 2 : 1) ? 'step' : undefined}><span>{i + 1}.</span> {text}</li>)}</ol><span className="access mono">{config ? `${config.report_retention_hours}-hour report access` : 'Same-tab report access'}</span></div></header>
    <main id="main">
      {!session ? <>
        <div className="intro"><span className="eyebrow">Evidence before publication</span><h1>Verify factual claims in your draft PDF</h1><p>Upload a marketing brief, whitepaper, or editorial report to extract claims in document order and check them against primary sources.</p></div>
        <div className="upload-grid"><form className="card upload-form" onSubmit={submit} aria-busy={busy}>
          <h2>Document PDF file</h2>
          <div className="dropzone" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (!busy) { if (event.dataTransfer.files.length !== 1) setError('Choose one PDF at a time.'); else choose(event.dataTransfer.files[0]); } }}>
            <span className="file-icon" aria-hidden="true">↑</span><h3>Drop your PDF here</h3><p>or browse from your computer</p>
            <p className="small">PDF must contain selectable text.<br />Maximum {sizeLimit} MiB and {config?.max_pdf_pages ?? '…'} pages.</p>
            <label className={`button secondary file-picker ${busy || !config ? 'disabled' : ''}`}>Choose PDF file<input type="file" accept=".pdf,application/pdf" aria-label="Choose PDF file" aria-describedby="upload-help upload-error" disabled={busy || !config} onChange={event => choose(event.target.files[0])} /></label>
          </div>
          {file && <div className="selected"><div><strong>{file.name}</strong><div className="mono">{(file.size / 1048576).toFixed(2)} MiB · Page and text checks run after upload</div></div><span className="badge verified">Ready</span></div>}
          <div id="upload-error" role="alert">{error && <p className="banner danger">{error}</p>}</div>
          {!config && configError && <div className="banner">{configError} <button type="button" onClick={() => setRetry(value => value + 1)}>Retry connection</button></div>}
          <div className="form-end"><p id="upload-help" className="small">Report access stays in this tab for {config?.report_retention_hours ?? '…'} hours after completion. Keep this tab open.</p><button className="primary" disabled={!file || busy || !config}>{busy ? 'Uploading PDF…' : 'Start fact-check'}</button></div>
        </form><aside className="card requirements"><h2>Upload requirements</h2>{[['Selectable text required', 'Image-only scans cannot be verified. Make sure you can highlight words in your PDF.'], [`${sizeLimit} MiB maximum file size`, 'Larger files are rejected before upload starts.'], [`${config?.max_pdf_pages ?? '…'} pages maximum`, 'Split longer documents into smaller sections.'], [`${config?.report_retention_hours ?? '…'} hours of report access`, 'The staged PDF is removed after claim extraction. Reports expire after completion.']].map(([title, text], i) => <div key={title}><span className="requirement-number mono">0{i + 1}</span><section><h3>{title}</h3><p>{text}</p></section></div>)}</aside></div>
      </> : <>
        <div className="report-title"><div><p className="mono filename">{session.filename || 'PDF document'}</p><h1 ref={heading} tabIndex={-1}>{unavailable ? 'Report unavailable' : done ? 'Fact-check report' : 'Checking your document'}</h1><p className="muted" role="status">{done ? report.state === 'completed_with_gaps' ? 'Finished with coverage gaps' : report.state === 'failed' ? 'Processing stopped' : 'Check complete' : report?.state === 'queued' ? 'Queued — waiting for a worker' : 'Saved findings appear as each check finishes.'}</p></div>{(done || unavailable) && <button className="secondary" onClick={reset}>Check another PDF</button>}</div>
        {pollError && <div className="banner" role="alert"><strong>{unavailable ? 'Report access ended' : 'Live updates interrupted'}</strong><p>{pollError}</p>{!unavailable && <button onClick={() => setRetry(value => value + 1)}>Retry connection</button>}</div>}
        {!done && !unavailable && <section className="card progress-card"><div className="progress-title"><h2>{report?.stage === 'checking' ? 'Verifying claims' : 'Reading PDF and identifying claims'}</h2><span className="mono" role="status">{report?.progress?.total ? `${report.progress.checked} of ${report.progress.total} claims checked` : 'Claim count not yet known'}</span></div><progress aria-label="Claims checked" {...(report?.progress?.total ? { max: report.progress.total, value: report.progress.checked } : {})} /><ol className="stages">{stages.map((text, i) => <li key={text} className={i < stageIndex ? 'finished' : i === stageIndex ? 'current' : ''}><span aria-hidden="true">{i < stageIndex ? '✓' : i + 1}</span>{text}<small>{i < stageIndex ? 'Completed' : report?.state === 'queued' ? 'Waiting' : i === stageIndex ? 'In progress' : i === 1 && stageIndex === 0 ? 'Part of text extraction' : 'Waiting'}</small></li>)}</ol></section>}
        {!!report?.coverage_gaps?.length && <section className="banner"><h2>Coverage gaps</h2><ul>{report.coverage_gaps.map((gap, i) => <li key={i}>{gap.code === 'unreadable_page' ? `Skipped page ${gap.page_number}: no selectable text. Claims on this page were not checked.` : gap.code === 'claim_limit_exceeded' ? `${gap.omitted_claims} claims omitted because the processing limit was reached.` : gap.code.replaceAll('_', ' ')}</li>)}</ul></section>}
        {report?.failure_code && <section className="banner danger"><h2>{(report.failure_code.startsWith('pdf_') || report.failure_code === 'invalid_pdf') ? 'Unable to read PDF text' : 'Processing could not finish'}</h2><p>{failureMessages[report.failure_code] || `The check stopped: ${report.failure_code.replaceAll('_', ' ')}. Try a smaller readable PDF or retry later.`}</p></section>}
        {!!claims.length && <><div className="findings-heading"><h2>{done ? 'Findings' : 'Available findings'}</h2><span className="mono">Document order · {claims.length} claims</span></div><div className="filters" aria-label="Filter findings">{[['all', 'All claims'], ...Object.entries(labels)].map(([key, label]) => <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label} <span>({key === 'all' ? claims.length : claims.filter(item => item.status !== 'pending' && (item.verdict || 'incomplete') === key).length})</span></button>)}</div><section aria-label="Findings" className="findings">{visible.map(item => <Finding key={item.sequence} item={item} />)}{!visible.length && <div className="card empty">No findings match this filter.</div>}</section></>}
        {!claims.length && done && report.state !== 'failed' && <section className="card empty"><span className="eyebrow">Document reviewed</span><h2>No checkable claims found</h2><p>No verifiable factual assertions were identified in the readable text. This does not establish that the document is accurate.{report.coverage_gaps?.length ? ' See the coverage gaps above for content that was not checked.' : ''}</p></section>}
      </>}
    </main><footer className="site-footer"><span className="brand-text">Proofline</span><span>Source-backed checks. Editorial judgment stays with you.</span></footer>
  </>;
}
createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>);
