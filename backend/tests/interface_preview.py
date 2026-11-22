"""Local-only Phase 10 browser fixture server; never mounted by the application.

Run: python -m uvicorn backend.tests.interface_preview:app --port 8010
Change local-notes/phase10/scenario.txt to select a response without live providers.
Scenarios: report, progress, transient, empty, unreadable, unavailable, upload_error.
"""
from copy import deepcopy
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.app.main import FRONTEND_DIST, public_config

app = FastAPI()
app.get('/api/config')(public_config)
SCENARIO = Path('local-notes/phase10/scenario.txt')

def scenario():
    return SCENARIO.read_text().strip() if SCENARIO.exists() else 'report'

@app.post('/api/runs')
async def upload():
    if scenario() == 'upload_error':
        return JSONResponse({'error': {'code': 'database_unavailable', 'message': 'Runs are temporarily unavailable.'}}, status_code=503)
    return JSONResponse({'run_id': 'fixture-run', 'access_token': 'local-fixture-only'}, status_code=202)

@app.get('/api/runs/{run_id}')
async def report(run_id: str, request: Request):
    case = scenario()
    if request.headers.get('authorization') != 'Bearer local-fixture-only' or case == 'unavailable':
        return JSONResponse({'error': {'code': 'report_unavailable', 'message': 'This report is unavailable.'}}, status_code=404)
    if case == 'transient':
        return JSONResponse({'error': {'code': 'database_unavailable', 'message': 'Runs are temporarily unavailable.'}}, status_code=503)
    claims = []
    for i, verdict in enumerate(['verified', 'inaccurate', 'false', None], 1):
        claims.append({'sequence': i, 'claim': {'excerpt': f'Synthetic claim {i}: revenue was 10 million USD in 2024.', 'page_number': i,
            'qualifiers': {'reporting_period': '2024'}}, 'status': 'incomplete' if verdict is None else 'completed',
            'verdict': verdict, 'explanation': 'The cited evidence supports the dated figure.' if verdict == 'verified' else 'The available evidence does not support the claim as written.',
            'reason': 'search_timeout' if verdict is None else None,
            'correction': {'text': 'Revenue was 12 million USD in 2025.', 'value': '12', 'unit': 'million USD', 'reporting_period': '2025'} if verdict == 'inaccurate' else None,
            'evidence': [] if verdict is None else [{'evidence_id': 'e1', 'source_url': 'https://example.com/', 'title': 'Synthetic source (fixture only)'}]})
    data = {'run_id': run_id, 'state': 'completed_with_gaps', 'stage': 'completed', 'failure_code': None,
            'coverage_gaps': [{'code': 'unreadable_page', 'page_number': 5}, {'code': 'claim_limit_exceeded', 'omitted_claims': 2}],
            'claims': claims, 'progress': {'total': 4, 'checked': 4}}
    if case == 'progress':
        data.update(state='running', stage='checking', coverage_gaps=[])
        data['claims'][-1].update(status='pending', verdict=None, correction=None, explanation=None, reason=None)
        data['progress']['checked'] = 3
    elif case in ('empty', 'unreadable'):
        data.update(state='completed' if case == 'empty' else 'failed', claims=[], progress={'total': 0, 'checked': 0}, coverage_gaps=[])
        if case == 'unreadable': data['failure_code'] = 'pdf_no_selectable_text'
    return deepcopy(data)

app.mount('/', StaticFiles(directory=FRONTEND_DIST, html=True))
