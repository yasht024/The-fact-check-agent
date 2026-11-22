"""Resumable PDF-to-report orchestration. Providers run outside transactions."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import psycopg

from backend.app.config import Settings
from backend.app.processing import job_queue, pipeline_store as store
from backend.app.processing.assessment import SearchContext, assess_claim, incomplete
from backend.app.processing.claims import ClaimExtractionError, extract_claims
from backend.app.processing.pdf_text import PdfExtractionError, extract_pdf_text
from backend.app.processing.retrieval import RetrievalError, retrieve_evidence
from backend.app.processing.search import SearchError, build_query, identify_primary_domain, search_sources

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Providers:
    parse: Callable = extract_pdf_text
    extract: Callable = extract_claims
    search: Callable = search_sources
    retrieve: Callable = retrieve_evidence
    assess: Callable = assess_claim


async def _check(database_url, lease, sequence, claim, providers, settings):
    evidence = {}
    failures = []
    seen_urls = set()
    deferred_urls = set()
    for phase in ('initial', 'follow_up'):
        # Prefer the identified publisher first, then broaden weak-evidence discovery.
        domain = identify_primary_domain(claim)
        query = build_query(claim, follow_up=phase == 'follow_up',
                            primary_domain=domain if phase == 'initial' else "")
        result = None
        for retry in range(2):
            attempt = await store.reserve_search(database_url, lease, sequence, phase, settings.max_searches_per_claim)
            if attempt is None:
                return incomplete('search_budget_exhausted'), evidence, failures
            try:
                result = await asyncio.to_thread(providers.search, query)
                await store.record_search(database_url, lease, sequence, attempt, 'completed')
                break
            except SearchError as error:
                await store.record_search(database_url, lease, sequence, attempt, error.code)
                logger.warning('Search failure for run %s claim %s code %s', lease.run_id, sequence, error.code)
                if retry == 0 and error.code in ('search_timeout', 'search_provider_error', 'search_rate_limited', 'search_request_failed'):
                    await asyncio.sleep(0.2)
                    continue
                return incomplete(error.code), evidence, failures
        # Primary publisher candidates rank first; cap material retrieval to three per pass.
        candidates = sorted(result.candidates, key=lambda item: (
            not urlsplit(item.url).hostname.endswith(('.gov', '.int')),
            urlsplit(item.url).path.casefold().endswith('.pdf'),
        ))
        candidates = [candidate for candidate in candidates if candidate.url not in seen_urls]
        deferred_urls.update(candidate.url for candidate in candidates[3:])
        for candidate in candidates[:3]:
            seen_urls.add(candidate.url)
            deferred_urls.discard(candidate.url)
            try:
                passages = await asyncio.to_thread(providers.retrieve, candidate, claim)
                for passage in passages[:5]:
                    if passage not in evidence.values():
                        evidence[f'e{len(evidence)+1}'] = passage
            except RetrievalError as error:
                failures.append({'source_url': candidate.url, 'code': error.code})
        context = SearchContext(True, phase == 'follow_up', 'material_source_unavailable' if failures else None)
        if not evidence:
            finding = assess_claim(claim, evidence, context)
        else:
            for retry in range(2):
                if not await store.reserve_assessment(database_url, lease, sequence):
                    return incomplete('assessment_budget_exhausted'), evidence, failures
                finding = await asyncio.to_thread(providers.assess, claim, evidence, context)
                if finding.reason:
                    logger.warning('Assessment failure for run %s claim %s code %s', lease.run_id, sequence, finding.reason)
                if retry == 0 and finding.reason in ('assessment_timeout', 'assessment_rate_limited', 'assessment_provider_error'):
                    await asyncio.sleep(0.2)
                    continue
                break
        if finding.verdict == 'false' and not finding.evidence_ids and deferred_urls:
            failures.extend({'source_url': url, 'code': 'source_budget_exhausted'} for url in sorted(deferred_urls))
            return incomplete('source_budget_exhausted'), evidence, failures
        if finding.status == 'completed' or context.follow_up_completed:
            return finding, evidence, failures
        if finding.reason not in ('follow_up_required', 'evidence_unresolved', 'missing_evidence', 'reporting_period_mismatch', 'material_source_unavailable'):
            return finding, evidence, failures
    return incomplete('evidence_unresolved'), evidence, failures


async def process_pipeline(database_url, lease, providers=None):
    providers = providers or Providers()
    settings = Settings.from_env()
    try:
        stage, remaining = await store.start(database_url, lease, settings.processing_deadline_seconds)
    except store.LeaseLost:
        return
    try:
        async with asyncio.timeout(max(0, float(remaining))):
            if stage != 'checking':
                logger.info('Extracting run %s', lease.run_id)
                pdf = await store.upload(database_url, lease)
                if pdf is None:
                    await store.finish(database_url, lease, settings.report_retention_hours, 'upload_unavailable')
                    return
                text = await asyncio.to_thread(providers.parse, pdf, max_pages=settings.max_pdf_pages)
                claims = await asyncio.to_thread(providers.extract, text.pages)
                gaps = [{'code': 'unreadable_page', 'page_number': page} for page in text.skipped_pages]
                if len(claims) > settings.max_claims:
                    gaps.append({'code': 'claim_limit_exceeded', 'omitted_claims': len(claims)-settings.max_claims})
                await store.save_claims(database_url, lease, claims[:settings.max_claims], gaps)
                logger.info('Saved extraction checkpoint for run %s', lease.run_id)
            for sequence, claim in await store.pending(database_url, lease):
                started_at = time.monotonic()
                try:
                    finding, evidence, failures = await _check(database_url, lease, sequence, claim, providers, settings)
                except (store.LeaseLost, psycopg.Error):
                    raise
                except Exception:
                    logger.error('Check failed for run %s claim %s', lease.run_id, sequence)
                    finding, evidence, failures = incomplete('check_failed'), {}, []
                await store.save_finding(database_url, lease, sequence, finding, evidence, failures)
                logger.info('Saved run %s claim %s status %s duration_seconds %.3f',
                            lease.run_id, sequence, finding.status, time.monotonic()-started_at)
            await store.finish(database_url, lease, settings.report_retention_hours)
    except TimeoutError:
        await store.finish(database_url, lease, settings.report_retention_hours, 'processing_deadline_exceeded')
    except PdfExtractionError as error:
        await store.finish(database_url, lease, settings.report_retention_hours, error.code)
    except ClaimExtractionError as error:
        if error.code in ('model_timeout', 'model_request_failed', 'model_rate_limited', 'model_provider_error') and lease.attempt_count < job_queue.MAX_ATTEMPTS:
            await job_queue.retry_run(database_url, lease)
        else:
            await store.finish(database_url, lease, settings.report_retention_hours, error.code)
    except store.LeaseLost:
        return
    except psycopg.Error:
        raise
    except Exception:
        logger.error('Pipeline failed for run %s', lease.run_id)
        await store.finish(database_url, lease, settings.report_retention_hours, 'pipeline_failed')
