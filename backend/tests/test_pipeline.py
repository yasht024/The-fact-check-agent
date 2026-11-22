"""Real PostgreSQL/API/worker integration with synthetic provider responses."""
import asyncio
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app.main import app
from backend.app.processing import job_queue, pipeline, pipeline_store, worker
from backend.app.processing.assessment import SearchContext, assess_claim
from backend.app.processing.claims import ClaimExtractionError, extract_claims
from backend.app.processing.retrieval import RetrievalError, retrieve_evidence, SourceDocument
from backend.app.processing.search import Candidate, SearchError, SearchResult
from backend.tests.test_assessment import answer
from backend.tests.test_pdf_text import pdf_fixture
from backend.tests.test_search import fixture_claim

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')
MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'
FIXTURES = Path(__file__).parent / 'fixtures'


@unittest.skipUnless(TEST_DATABASE_URL, 'Set TEST_DATABASE_URL to run pipeline PostgreSQL acceptance checks.')
class PipelinePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        schema_name = f'pipeline_test_{uuid4().hex}'
        admin = await self.enterAsyncContext(await psycopg.AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True))
        await admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema_name)))
        self.addAsyncCleanup(admin.execute, sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema_name)))
        self.database_url = make_conninfo(TEST_DATABASE_URL, options=f'-csearch_path={schema_name}')
        self.db = await self.enterAsyncContext(await psycopg.AsyncConnection.connect(self.database_url, autocommit=True))
        for migration in sorted(MIGRATIONS.glob('*.sql')):
            await self.db.execute(migration.read_text())
        self.enterContext(patch.dict(os.environ, {'DATABASE_URL': self.database_url, 'PROCESSING_DEADLINE_SECONDS': '60',
                                                 'MAX_CLAIMS': '50', 'MAX_SEARCHES_PER_CLAIM': '4'}))
        self.client = await self.enterAsyncContext(AsyncClient(transport=ASGITransport(app=app), base_url='http://test'))
        self.claims = [fixture_claim()]
        self.search_queries = []
        self.extract_calls = 0
        self.providers = pipeline.Providers(extract=self.extract, search=self.search, retrieve=self.retrieve,
                                            assess=lambda c, e, context: assess_claim(c, e, context, provider=lambda c, e: answer()))

    def extract(self, pages):
        import json
        self.extract_calls += 1
        payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': json.dumps({'claims': [claim.model_dump() for claim in self.claims]})}]}}]}
        return extract_claims(pages, provider=lambda page, text: payload)

    def search(self, query):
        self.search_queries.append(query)
        return SearchResult(query, (Candidate('https://public.example/report', 'Report'),))

    def retrieve(self, candidate, claim):
        document = SourceDocument(candidate.url, (FIXTURES/'source.html').read_bytes(), 'text/html', '2026-11-23T00:00:00+00:00')
        return retrieve_evidence(candidate, claim, fetcher=lambda u: document)

    async def upload(self, pdf=None):
        pdf = pdf if pdf is not None else pdf_fixture(' '.join(claim.excerpt for claim in self.claims))
        response = await self.client.post('/api/runs', files={'file': ('fixture.pdf', pdf, 'application/pdf')})
        self.assertEqual(response.status_code, 202)
        self.run_id = UUID(response.json()['run_id'])
        self.token = response.json()['access_token']
        return response.json()

    async def report(self):
        return await self.client.get(f'/api/runs/{self.run_id}', headers={'Authorization': f'Bearer {self.token}'})

    async def scalar(self, query, values=None):
        cursor = await self.db.execute(query, values or (self.run_id,))
        return (await cursor.fetchone())[0]

    async def test_upload_produces_saved_report_and_removes_pdf(self):
        await self.upload()
        self.assertTrue(await worker.process_one(self.database_url, providers=self.providers))
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed')
        self.assertEqual(report['progress'], {'total': 1, 'checked': 1})
        self.assertEqual(report['claims'][0]['verdict'], 'verified')
        self.assertIn('42 TWh', report['claims'][0]['evidence'][0]['passage'])
        self.assertEqual(report['claims'][0]['evidence'][0]['source_url'], 'https://public.example/report')
        self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 0)
        self.assertEqual(await self.scalar('SELECT count(*) FROM claim_checks WHERE run_id=%s'), 1)

    async def test_failed_check_preserves_other_results(self):
        self.claims.append(fixture_claim().model_copy(update={'check_target': 'Failing solar production check'}))
        def search(query):
            if 'Failing' in query:
                raise SearchError('search_timeout')
            return self.search(query)
        await self.upload()
        with self.assertLogs(pipeline.logger, 'WARNING'):
            await worker.process_one(self.database_url, providers=replace(self.providers, search=search))
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed_with_gaps')
        self.assertEqual([c['verdict'] for c in report['claims']], ['verified', None])
        self.assertEqual(report['claims'][1]['reason'], 'search_timeout')
        self.assertEqual(report['claims'][1]['search_attempts'], 2)
        self.assertTrue(report['claims'][0]['evidence'])

    async def test_restart_skips_committed_findings_and_rejects_stale_writes(self):
        self.claims.append(fixture_claim().model_copy(update={'check_target': 'Second solar production check'}))
        await self.upload()
        original = await job_queue.claim_run(self.database_url)
        ready = asyncio.Event()
        async def save_and_pause(*args, **kwargs):
            await real_save(*args, **kwargs)
            ready.set()
            await asyncio.Future()
        real_save = pipeline_store.save_finding
        with patch.object(pipeline_store, 'save_finding', side_effect=save_and_pause):
            processing = asyncio.create_task(pipeline.process_pipeline(self.database_url, original, self.providers))
            try:
                await asyncio.wait_for(ready.wait(), 10)
                report = (await self.report()).json()
                self.assertEqual(report['progress'], {'total': 2, 'checked': 1})
                self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 0)
            finally:
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)
        await self.db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s", (self.run_id,))
        with self.assertRaises(pipeline_store.LeaseLost):
            await real_save(self.database_url, original, 2, None, {}, [])
        await worker.process_one(self.database_url, providers=self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed')
        self.assertEqual([c['verdict'] for c in report['claims']], ['verified', 'verified'])
        self.assertEqual(self.extract_calls, 1)
        self.assertEqual(len(self.search_queries), 2)
        self.assertEqual(await self.scalar('SELECT count(*) FROM claims WHERE run_id=%s'), 2)
        self.assertEqual(await self.scalar('SELECT count(*) FROM claim_checks WHERE run_id=%s'), 2)
        expected_passages = sum(len(self.retrieve(Candidate('https://public.example/report', 'Report'), claim)) for claim in self.claims)
        self.assertEqual(await self.scalar('SELECT count(*) FROM evidence WHERE run_id=%s'), expected_passages)
        self.assertEqual(await self.scalar('SELECT count(DISTINCT (sequence, evidence_id)) FROM evidence WHERE run_id=%s'), expected_passages)
        self.assertFalse(await worker.process_one(self.database_url, providers=self.providers))

    async def test_completed_empty_search_and_follow_up_are_false(self):
        await self.upload()
        queries = []
        def empty(query):
            queries.append(query)
            return SearchResult(query, ())
        await worker.process_one(self.database_url, providers=replace(self.providers, search=empty))
        report = (await self.report()).json()
        self.assertEqual(report['claims'][0]['verdict'], 'false')
        self.assertEqual(report['claims'][0]['search_attempts'], 2)
        self.assertNotEqual(queries[0], queries[1])
        self.assertEqual(report['claims'][0]['evidence'], [])

    async def test_material_source_failure_is_not_false(self):
        await self.upload()
        def unavailable(*args):
            raise RetrievalError('source_unavailable')
        await worker.process_one(self.database_url, providers=replace(self.providers, retrieve=unavailable))
        report = (await self.report()).json()
        self.assertIsNone(report['claims'][0]['verdict'])
        self.assertEqual(report['claims'][0]['reason'], 'material_source_unavailable')
        self.assertEqual(report['claims'][0]['source_failures'][0]['code'], 'source_unavailable')

    async def test_blocked_candidate_keeps_failure_but_does_not_hide_cited_finding(self):
        await self.upload()
        blocked = Candidate('https://blocked.example/report', 'Blocked candidate')
        good = Candidate('https://public.example/report', 'Public report')
        def retrieve(candidate, claim):
            if candidate == blocked:
                raise RetrievalError('source_unavailable')
            return self.retrieve(candidate, claim)
        await worker.process_one(self.database_url, providers=replace(self.providers,
            search=lambda query: SearchResult(query, (blocked, good)), retrieve=retrieve))
        finding = (await self.report()).json()['claims'][0]
        self.assertEqual(finding['verdict'], 'verified')
        self.assertTrue(finding['evidence'])
        self.assertEqual(finding['source_failures'], [{'source_url': blocked.url, 'code': 'source_unavailable'}])

    async def test_partial_retrieval_cannot_establish_absence_of_support(self):
        await self.upload()
        def retrieve(candidate, claim):
            if candidate.url.startswith('https://blocked.'):
                raise RetrievalError('source_unavailable')
            return self.retrieve(candidate, claim)
        providers = replace(self.providers,
            search=lambda query: SearchResult(query, (Candidate('https://blocked.example/report', 'Blocked'),
                Candidate('https://public.example/report', 'Report'))), retrieve=retrieve,
            assess=lambda c, e, context: assess_claim(c, e, context, provider=lambda c, e: answer('unsupported', [])))
        await worker.process_one(self.database_url, providers=providers)
        finding = (await self.report()).json()['claims'][0]
        self.assertIsNone(finding['verdict'])
        self.assertEqual(finding['reason'], 'material_source_unavailable')

    async def test_deadline_preserves_committed_results(self):
        self.claims.append(fixture_claim().model_copy(update={'check_target': 'Second solar production check'}))
        await self.upload()
        lease = await job_queue.claim_run(self.database_url)
        await pipeline_store.start(self.database_url, lease, 60)
        await pipeline_store.save_claims(self.database_url, lease, self.claims, [])
        evidence = self.retrieve(Candidate('https://public.example/report', 'Report'), self.claims[0])
        finding = self.providers.assess(self.claims[0], {'e1': evidence[0]}, SearchContext(True))
        await pipeline_store.save_finding(self.database_url, lease, 1, finding, {'e1': evidence[0]}, [])
        await self.db.execute("UPDATE runs SET processing_deadline=clock_timestamp()-interval '1 second' WHERE run_id=%s", (self.run_id,))
        await pipeline.process_pipeline(self.database_url, lease, self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed_with_gaps')
        self.assertEqual([c['verdict'] for c in report['claims']], ['verified', None])
        self.assertEqual(report['claims'][1]['reason'], 'processing_deadline_exceeded')

    async def test_terminal_extraction_failure_removes_pdf(self):
        await self.upload(pdf=b'%PDF-broken')
        await worker.process_one(self.database_url, providers=self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['failure_code'], 'invalid_pdf')
        self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 0)

    async def test_report_expiry_is_enforced_before_cleanup_and_cascades(self):
        await self.upload()
        await worker.process_one(self.database_url, providers=self.providers)
        await self.db.execute("UPDATE runs SET expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s", (self.run_id,))
        self.assertEqual((await self.report()).status_code, 404)
        await pipeline_store.cleanup(self.database_url, 60, 24)
        for table in ('runs', 'uploads', 'claims', 'claim_checks', 'evidence'):
            self.assertEqual(await self.scalar(f'SELECT count(*) FROM {table} WHERE run_id=%s'), 0)

    async def test_empty_document_and_mixed_pdf_coverage(self):
        await self.upload(pdf=pdf_fixture('Promotional material only.'))
        await worker.process_one(self.database_url, providers=replace(self.providers, extract=lambda pages: []))
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed')
        self.assertEqual(report['claims'], [])
        await self.upload(pdf=pdf_fixture(self.claims[0].excerpt, None))
        await worker.process_one(self.database_url, providers=self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed_with_gaps')
        self.assertEqual(report['coverage_gaps'], [{'code': 'unreadable_page', 'page_number': 2}])
        self.assertEqual(report['claims'][0]['verdict'], 'verified')

    async def test_search_budget_survives_restart(self):
        await self.upload()
        lease = await job_queue.claim_run(self.database_url)
        await pipeline_store.start(self.database_url, lease, 60)
        await pipeline_store.save_claims(self.database_url, lease, self.claims, [])
        for _ in range(4):
            self.assertIsNotNone(await pipeline_store.reserve_search(self.database_url, lease, 1, 'initial', 4))
        await self.db.execute("UPDATE runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=%s", (self.run_id,))
        await worker.process_one(self.database_url, providers=self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['claims'][0]['reason'], 'search_budget_exhausted')
        self.assertEqual(report['claims'][0]['search_attempts'], 4)
        self.assertEqual(self.search_queries, [])

    async def test_expired_queued_run_is_closed_and_pdf_removed(self):
        await self.upload()
        await self.db.execute("UPDATE runs SET created_at=clock_timestamp()-interval '2 minutes' WHERE run_id=%s", (self.run_id,))
        self.assertFalse(await worker.process_one(self.database_url, providers=self.providers))
        self.assertEqual((await self.report()).json()['state'], 'failed')
        self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 0)

    async def test_claim_limit_records_coverage_gap(self):
        self.claims.append(fixture_claim().model_copy(update={'check_target': 'Second solar production check'}))
        await self.upload()
        with patch.dict(os.environ, {'MAX_CLAIMS': '1'}):
            await worker.process_one(self.database_url, providers=self.providers)
        report = (await self.report()).json()
        self.assertEqual(report['state'], 'completed_with_gaps')
        self.assertEqual(report['coverage_gaps'], [{'code': 'claim_limit_exceeded', 'omitted_claims': 1}])

    async def test_finding_and_evidence_write_roll_back_together(self):
        await self.upload()
        lease = await job_queue.claim_run(self.database_url)
        await pipeline_store.start(self.database_url, lease, 60)
        await pipeline_store.save_claims(self.database_url, lease, self.claims, [])
        source = self.retrieve(Candidate('https://public.example/report', 'Report'), self.claims[0])[0]
        finding = self.providers.assess(self.claims[0], {'e1': source}, SearchContext(True))
        bad_source = replace(source, retrieved_at='invalid timestamp')
        with self.assertRaises(psycopg.Error):
            await pipeline_store.save_finding(self.database_url, lease, 1, finding, {'e1': source, 'e2': bad_source}, [])
        self.assertEqual(await self.scalar('SELECT count(*) FROM evidence WHERE run_id=%s'), 0)
        self.assertEqual(await self.scalar('SELECT status FROM claim_checks WHERE run_id=%s'), 'pending')
        await pipeline_store.save_finding(self.database_url, lease, 1, finding, {'e1': source}, [])
        await pipeline_store.save_finding(self.database_url, lease, 1, finding, {'e1': source}, [])
        self.assertEqual(await self.scalar('SELECT count(*) FROM evidence WHERE run_id=%s'), 1)

    async def test_transient_extraction_failure_retries_without_deleting_pdf(self):
        await self.upload()
        def failing(pages):
            raise ClaimExtractionError('model_request_failed')
        await worker.process_one(self.database_url, providers=replace(self.providers, extract=failing))
        self.assertEqual((await self.report()).json()['state'], 'queued')
        self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 1)
        await self.db.execute("UPDATE runs SET next_attempt_at=clock_timestamp() WHERE run_id=%s", (self.run_id,))
        await worker.process_one(self.database_url, providers=self.providers)
        self.assertEqual((await self.report()).json()['claims'][0]['verdict'], 'verified')
        self.assertEqual(await self.scalar('SELECT count(*) FROM uploads WHERE run_id=%s'), 0)

    async def test_assessment_retry_and_budget_are_persisted(self):
        from backend.app.processing.assessment import incomplete
        await self.upload()
        calls = []
        def assess(claim, evidence, context):
            calls.append(1)
            if len(calls) == 1:
                return incomplete('assessment_timeout')
            return self.providers.assess(claim, evidence, context)
        with self.assertLogs(pipeline.logger, 'WARNING'):
            await worker.process_one(self.database_url, providers=replace(self.providers, assess=assess))
        report = (await self.report()).json()
        self.assertEqual(report['claims'][0]['verdict'], 'verified')
        self.assertEqual(report['claims'][0]['assessment_attempts'], 2)

    async def test_unknown_model_evidence_ids_are_never_published(self):
        await self.upload()
        def assess(claim, evidence, context):
            return assess_claim(claim, evidence, context, provider=lambda c, e: answer(ids=['unknown']))
        with self.assertLogs(pipeline.logger, 'WARNING'):
            await worker.process_one(self.database_url, providers=replace(self.providers, assess=assess))
        claim = (await self.report()).json()['claims'][0]
        self.assertEqual(claim['reason'], 'unknown_evidence_id')
        self.assertIsNone(claim['verdict'])
        self.assertEqual(claim['evidence'], [])

    async def test_pipeline_renews_lease_during_blocking_provider(self):
        import threading
        from datetime import timedelta
        await self.upload()
        started = threading.Event()
        release = threading.Event()
        def extract(pages):
            started.set()
            release.wait(10)
            return self.extract(pages)
        processing = asyncio.create_task(worker.process_one(self.database_url, providers=replace(self.providers, extract=extract),
                                                           lease_duration=timedelta(seconds=3)))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            expiry = await self.scalar('SELECT lease_expires_at FROM runs WHERE run_id=%s')
            async def wait_for_renewal():
                while (await self.scalar('SELECT lease_expires_at FROM runs WHERE run_id=%s')) <= expiry:
                    await asyncio.sleep(0.05)
            await asyncio.wait_for(wait_for_renewal(), 5)
            self.assertIsNone(await job_queue.claim_run(self.database_url))
            release.set()
            await asyncio.wait_for(processing, 5)
        finally:
            release.set()
            if not processing.done():
                processing.cancel()
                await asyncio.gather(processing, return_exceptions=True)
        self.assertEqual((await self.report()).json()['state'], 'completed')

    async def test_unread_candidates_prevent_no_support_false(self):
        await self.upload()
        def candidates(query):
            return SearchResult(query, tuple(Candidate(f'https://public.example/report/{index}', 'Report') for index in range(7)))
        retrieved = []
        def no_passages(candidate, claim):
            retrieved.append(candidate.url)
            return ()
        await worker.process_one(self.database_url, providers=replace(self.providers, search=candidates, retrieve=no_passages))
        claim = (await self.report()).json()['claims'][0]
        self.assertIsNone(claim['verdict'])
        self.assertEqual(claim['reason'], 'source_budget_exhausted')
        self.assertEqual(len(retrieved), 6)
        self.assertEqual(len(set(retrieved)), 6)

    async def test_primary_publisher_search_broadens_on_follow_up(self):
        self.claims = [fixture_claim().model_copy(update={'check_target': 'Amazon solar energy production'})]
        await self.upload()
        queries = []
        def empty(query):
            queries.append(query)
            return SearchResult(query, ())
        await worker.process_one(self.database_url, providers=replace(self.providers, search=empty))
        self.assertIn('site:ir.aboutamazon.com', queries[0])
        self.assertNotIn('site:', queries[1])
