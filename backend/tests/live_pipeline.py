"""Opt-in local API + worker + real-provider verification using public synthetic data.

Load .env into the shell first. This is not part of unittest discovery.
No credentials or report access tokens are printed or saved.
"""
import json
import logging
import os
import subprocess
import sys
import time
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
import psycopg
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from backend.app.config import Settings
from backend.app.processing.claims import Claim
from backend.app.processing.retrieval import retrieve_evidence
from backend.app.processing.search import Candidate
from backend.tests.test_pdf_text import pdf_fixture

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'local-notes/phase9-live'
BASE = 'http://127.0.0.1:8009'
DATABASE = 'postgresql://factcheck:factcheck_dev@127.0.0.1:55432/factcheck'


def synthetic_pdf():
    content = (
        b'BT /F1 12 Tf 40 740 Td '
        b"(For calendar year 2024, Amazon reported full-year net sales) Tj 0 -18 Td "
        b"(of $638.0 billion.) Tj 0 -30 Td "
        b"(For the latest completed calendar year, Amazon's full-year) Tj 0 -18 Td "
        b"(net sales are $638.0 billion.) Tj ET"
    )
    writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf_fixture(content))))
    writer.pages[0].mediabox = RectangleObject([0, 0, 612, 792])
    writer.add_metadata({'/Title': 'Local Phase 9 public-data verification'})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def verify(report, database_url):
    assert report['state'] == 'completed', 'Expected a complete report.'
    assert len(report['claims']) == 2, 'Expected two independent claims.'
    historical, current = report['claims']
    assert historical['verdict'] == 'verified', 'Historical claim must stay Verified.'
    assert current['verdict'] == 'inaccurate', 'Outdated current figure must be Inaccurate.'
    correction = current['correction']
    assert correction and Decimal(correction['value'].replace(',', '')) == Decimal('716.9'), 'Expected the 2025 $716.9 billion replacement.'
    assert correction['reporting_period'] == '2025', 'Expected correction reporting period 2025.'
    assert 'billion' in correction['unit'].lower(), 'Expected billion-dollar units.'
    for finding in report['claims']:
        assert finding['evidence'], 'Expected validated source links.'
        claim = Claim.model_validate(finding['claim'])
        for source in finding['evidence']:
            assert source['source_url'].startswith('https://'), 'Expected a public HTTPS citation.'
            fresh = retrieve_evidence(Candidate(source['source_url'], source['title']), claim)
            normalized = ' '.join(source['passage'].split())
            assert any(normalized == ' '.join(item.passage.split()) for item in fresh), 'Cited passage was not reproduced from its live source.'
    cited = next(item for item in current['evidence'] if item['evidence_id'] == correction['evidence_id'])
    assert '716.9' in cited['passage'] and '2025' in cited['passage'], 'Correction source must establish value and period.'
    assert '638.0' in ' '.join(item['passage'] for item in historical['evidence']), 'Historical source must establish 2024 figure.'
    with psycopg.connect(database_url, connect_timeout=5) as db:
        assert db.execute('SELECT count(*) FROM uploads WHERE run_id=%s', (report['run_id'],)).fetchone()[0] == 0, 'Staged PDF was not removed.'
        assert db.execute('SELECT count(*) FROM claim_checks WHERE run_id=%s', (report['run_id'],)).fetchone()[0] == 2, 'Expected two persisted findings.'


def main():
    settings = Settings.from_env()
    if not settings.gemini_api_key or not settings.tavily_api_key:
        raise SystemExit('Live check pending: GEMINI_API_KEY and TAVILY_API_KEY are required.')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, DATABASE_URL=settings.database_url or DATABASE)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    processes = []
    pdf = synthetic_pdf()
    (OUTPUT/'input.pdf').write_bytes(pdf)
    with (OUTPUT/'services.log').open('a') as log:
        try:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1',
                                       '--port', '8009', '--no-access-log', '--log-level', 'warning'],
                                      cwd=ROOT, env=environment, stdout=log, stderr=log)
            processes.append(server)
            with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as client:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Local verification API could not start.')
                    try:
                        if client.get('/healthz').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError('Local verification API did not become ready.')
                response = client.post('/api/runs', files={'file': ('public-fixture.pdf', pdf, 'application/pdf')})
                assert response.status_code == 202, 'Upload API did not accept the PDF.'
                accepted = response.json()
                run_id = accepted['run_id']
                headers = {'Authorization': f"Bearer {accepted['access_token']}"}
                print('Live upload accepted; run_id:', run_id, flush=True)
                processes.append(subprocess.Popen([sys.executable, '-m', 'backend.app.processing.worker'],
                                                  cwd=ROOT, env=environment, stdout=log, stderr=log))
                deadline = time.monotonic() + settings.processing_deadline_seconds + 30
                while time.monotonic() < deadline:
                    response = client.get(f'/api/runs/{run_id}', headers=headers)
                    assert response.status_code == 200, 'Report endpoint could not read the saved run.'
                    report = response.json()
                    if report['state'] in ('completed', 'completed_with_gaps', 'failed'):
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError('Live run did not finish before the verification deadline.')
            (OUTPUT/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
            (OUTPUT/f"report-{report['run_id']}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
            print(json.dumps({'state': report['state'], 'failure_code': report['failure_code'], 'findings': [
                {'sequence': item['sequence'], 'verdict': item['verdict'], 'reason': item['reason'],
                 'correction': {key: item['correction'][key] for key in ('value', 'unit', 'reporting_period')} if item['correction'] else None, 'sources': [source['source_url'] for source in item['evidence']],
                 'source_failures': item['source_failures']} for item in report['claims']]}), flush=True)
            verify(report, environment['DATABASE_URL'])
            print('Live local Phase 9 verification passed: historical Verified, outdated Inaccurate, source-backed 2025 correction, persisted findings, and removed PDF.', flush=True)
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    try:
        main()
    except AssertionError as error:
        raise SystemExit(f'Live verification failed: {error}') from None
    except Exception as error:
        code = getattr(error, 'code', type(error).__name__)
        raise SystemExit(f'Live verification blocked: {code}. Inspect local artifacts; secret values are withheld.') from None
