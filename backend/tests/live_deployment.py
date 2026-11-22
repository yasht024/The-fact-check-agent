"""Opt-in public HTTP smoke check. Never prints or saves the report token.

Run locally: python -m backend.tests.live_deployment --base-url https://PUBLIC-FRONTEND
This tests HTTP, not a browser. Browser/keyboard/layout checks remain separate.
"""
import argparse
import json
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from backend.tests.test_pdf_text import pdf_fixture
from pathlib import Path
import time
from urllib.parse import urlparse
import httpx
from backend.tests.live_pipeline import synthetic_pdf
from backend.app.processing.claims import Claim
from backend.app.processing.retrieval import retrieve_evidence
from backend.app.processing.search import Candidate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--fixture', choices=['nasa', 'amazon'], default='nasa')
    parser.add_argument('--output', type=Path, default=Path('local-notes/phase11/public-report.json'))
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit('Use a public HTTPS origin without credentials or a query.')
    with httpx.Client(base_url=base, timeout=20, trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        response = client.get('/healthz')
        assert response.status_code == 200 and response.json() == {'status': 'ok'}, 'Public health check failed.'
        assert client.get('/api/config').status_code == 200, 'Public config route failed.'
        page = client.get('/')
        assert page.status_code == 200 and 'Proofline' in page.text, 'Public frontend failed.'
        invalid = client.post('/api/runs', files={'file': ('invalid.pdf', b'not a pdf', 'application/pdf')})
        assert invalid.status_code == 400, 'Invalid upload was not rejected.'
        pdf = synthetic_pdf()
        if args.fixture == 'nasa':
            writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf_fixture(b"BT /F1 12 Tf 40 740 Td (Earth's equatorial diameter is 12,756 kilometers.) Tj ET"))))
            writer.pages[0].mediabox = RectangleObject([0, 0, 612, 792])
            writer.add_metadata({'/Title': 'Phase 11 public NASA fact check'})
            output = BytesIO(); writer.write(output); pdf = output.getvalue()
        upload = client.post('/api/runs', files={'file': ('phase11-public.pdf', pdf, 'application/pdf')})
        assert upload.status_code == 202, f'Public upload failed: HTTP {upload.status_code}.'
        accepted = upload.json()
        run_id = accepted['run_id']
        headers = {'Authorization': f"Bearer {accepted['access_token']}"}
        print('Public upload accepted; run_id:', run_id, flush=True)
        assert client.get(f'/api/runs/{run_id}').status_code == 404, 'Report must require its bearer token.'
        previous = None
        deadline = time.monotonic() + 660
        while time.monotonic() < deadline:
            try:
                response = client.get(f'/api/runs/{run_id}', headers=headers)
            except httpx.TransportError:
                print('Temporary report connection error; retrying read.', flush=True)
                time.sleep(3)
                continue
            if response.status_code >= 500:
                print('Temporary report service error; retrying read.', flush=True)
                time.sleep(3)
                continue
            assert response.status_code == 200, f'Public report failed: HTTP {response.status_code}.'
            assert response.headers.get('cache-control') == 'no-store', 'Report must not be cached.'
            report = response.json()
            current = (report['state'], report['stage'], report['progress']['checked'])
            if current != previous:
                print('Progress:', *current, flush=True)
                previous = current
            if report['state'] in ('completed', 'completed_with_gaps', 'failed'): break
            time.sleep(3)
        else:
            raise SystemExit('Public report did not finish within its processing deadline.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        assert report['state'] in ('completed', 'completed_with_gaps'), 'Public processing failed.'
        supported = [item for item in report['claims'] if item['verdict'] and item['evidence']]
        print('Check reasons:', [item['reason'] for item in report['claims']], flush=True)
        assert supported, 'No finished finding has a citation; live acceptance remains pending.'
        verified_sources = []
        for finding in supported:
            claim = Claim.model_validate(finding['claim'])
            for source in finding['evidence']:
                passages = retrieve_evidence(Candidate(source['source_url'], source['title']), claim)
                quote = ' '.join(source['passage'].split())
                assert any(quote == ' '.join(item.passage.split()) for item in passages), 'Cited source passage could not be reproduced.'
                verified_sources.append(source['source_url'])
            correction = finding.get('correction')
            if correction:
                source = next(item for item in finding['evidence'] if item['evidence_id'] == correction['evidence_id'])
                normalized = ' '.join(source['passage'].split())
                assert correction['value'] in normalized and correction['reporting_period'] in normalized, 'Correction source does not contain its value/period.'
        print('Public report:', report['state'], flush=True)
        print('Verdicts:', [item['verdict'] for item in report['claims']], flush=True)
        print('Cited source passages reproduced:', len(verified_sources), flush=True)
        for source in sorted(set(verified_sources)): print('Verified citation:', source, flush=True)
        print('HTTP acceptance passed. Fresh-browser acceptance is a separate check.', flush=True)


if __name__ == '__main__':
    main()
