"""Lease-fenced, transactional pipeline checkpoints and per-claim findings."""
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from backend.app.processing.claims import Claim
from backend.app.processing.job_queue import RunLease


class LeaseLost(Exception):
    pass


@asynccontextmanager
async def locked(database_url: str, lease: RunLease):
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        cursor = await connection.execute(
            "SELECT 1 FROM runs WHERE run_id=%s AND lease_id=%s AND state='running' FOR UPDATE",
            (lease.run_id, lease.lease_id))
        if not await cursor.fetchone():
            raise LeaseLost
        cursor = await connection.execute(
            "SELECT 1 FROM runs WHERE run_id=%s AND lease_expires_at > clock_timestamp()", (lease.run_id,))
        if not await cursor.fetchone():
            raise LeaseLost
        yield connection


async def start(database_url, lease, seconds):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute(
            """UPDATE runs SET processing_deadline=COALESCE(processing_deadline, created_at + %s)
               WHERE run_id=%s RETURNING stage, EXTRACT(EPOCH FROM processing_deadline-clock_timestamp())""",
            (timedelta(seconds=seconds), lease.run_id))
        return await cursor.fetchone()


async def upload(database_url, lease):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute("SELECT pdf FROM uploads WHERE run_id=%s", (lease.run_id,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def save_claims(database_url, lease, claims, gaps):
    async with locked(database_url, lease) as connection:
        for sequence, claim in enumerate(claims, 1):
            await connection.execute("INSERT INTO claims VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                                     (lease.run_id, sequence, Jsonb(claim.model_dump())))
            await connection.execute("INSERT INTO claim_checks (run_id,sequence) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                     (lease.run_id, sequence))
        await connection.execute("UPDATE runs SET stage='checking', coverage_gaps=%s WHERE run_id=%s",
                                 (Jsonb(gaps), lease.run_id))
        await connection.execute("DELETE FROM uploads WHERE run_id=%s", (lease.run_id,))


async def pending(database_url, lease):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute(
            """SELECT c.sequence,c.claim FROM claims c JOIN claim_checks k USING (run_id,sequence)
               WHERE c.run_id=%s AND k.status='pending' ORDER BY c.sequence""", (lease.run_id,))
        return [(sequence, Claim.model_validate(claim)) for sequence, claim in await cursor.fetchall()]


async def reserve_search(database_url, lease, sequence, phase, limit):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute(
            """UPDATE claim_checks SET search_attempts=search_attempts+1,
                      search_events=search_events || %s
               WHERE run_id=%s AND sequence=%s AND status='pending' AND search_attempts < %s
               RETURNING search_attempts""",
            (Jsonb([{"phase": phase, "status": "started"}]), lease.run_id, sequence, limit))
        row = await cursor.fetchone()
        return row[0] if row else None


async def record_search(database_url, lease, sequence, attempt, code):
    async with locked(database_url, lease) as connection:
        await connection.execute(
            """UPDATE claim_checks SET search_events=jsonb_set(search_events, ARRAY[(%s-1)::text,'status'], %s)
               WHERE run_id=%s AND sequence=%s""",
            (attempt, Jsonb(code), lease.run_id, sequence))


async def save_finding(database_url, lease, sequence, finding, evidence, failures):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute(
            "SELECT status FROM claim_checks WHERE run_id=%s AND sequence=%s FOR UPDATE", (lease.run_id, sequence))
        row = await cursor.fetchone()
        if row is None or row[0] != 'pending':
            return
        for key, item in evidence.items():
            data = asdict(item)
            await connection.execute(
                """INSERT INTO evidence VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lease.run_id, sequence, key, data['source_url'], data['title'], data['retrieved_at'],
                 data['publication_date'], data['reporting_period'], data['passage'], data['page_number']))
        await connection.execute(
            """UPDATE claim_checks SET status=%s,verdict=%s,explanation=%s,reason=%s,correction=%s,
                 evidence_ids=%s, source_failures=%s,checked_at=clock_timestamp()
               WHERE run_id=%s AND sequence=%s""",
            (finding.status, finding.verdict, finding.explanation, finding.reason,
             Jsonb(finding.correction) if finding.correction else None, Jsonb(list(finding.evidence_ids)),
             Jsonb(failures), lease.run_id, sequence))


async def finish(database_url, lease, retention_hours, failure_code=None):
    async with locked(database_url, lease) as connection:
        if failure_code:
            await connection.execute(
                """UPDATE claim_checks SET status='incomplete',reason=%s,
                   explanation='The processing budget ended before this check completed.',checked_at=clock_timestamp()
                   WHERE run_id=%s AND status='pending'""", (failure_code, lease.run_id))
        cursor = await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM claims WHERE run_id=%s),stage,coverage_gaps FROM runs WHERE run_id=%s",
            (lease.run_id, lease.run_id))
        has_claims, stage, gaps = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT count(*) FROM claim_checks WHERE run_id=%s AND status <> 'completed'", (lease.run_id,))
        unfinished = (await cursor.fetchone())[0]
        if failure_code and not has_claims and stage != 'checking':
            state = 'failed'
        else:
            state = 'completed_with_gaps' if gaps or unfinished else 'completed'
        await connection.execute(
            """UPDATE runs SET state=%s,stage='completed',failure_code=%s,completed_at=clock_timestamp(),
               expires_at=clock_timestamp()+%s,lease_id=NULL,lease_expires_at=NULL WHERE run_id=%s""",
            (state, failure_code, timedelta(hours=retention_hours), lease.run_id))
        await connection.execute("DELETE FROM uploads WHERE run_id=%s", (lease.run_id,))


async def cleanup(database_url, deadline_seconds, retention_hours):
    """Only close expired jobs without a live owner; cascade expired reports."""
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        cursor = await connection.execute(
            """SELECT run_id,stage FROM runs WHERE state IN ('queued','running')
               AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
               AND (COALESCE(processing_deadline,created_at+%s)<=clock_timestamp() OR attempt_count>=3)
               FOR UPDATE SKIP LOCKED""", (timedelta(seconds=deadline_seconds),))
        for run_id, stage in await cursor.fetchall():
            await connection.execute(
                """UPDATE claim_checks SET status='incomplete',reason='processing_budget_exhausted',
                    explanation='The processing budget ended before this check completed.',checked_at=clock_timestamp()
                    WHERE run_id=%s AND status='pending'""", (run_id,))
            await connection.execute(
                """UPDATE runs SET state=%s,stage='completed',failure_code='processing_budget_exhausted',
                    completed_at=clock_timestamp(),expires_at=clock_timestamp()+%s,
                    lease_id=NULL,lease_expires_at=NULL WHERE run_id=%s""",
                ('completed_with_gaps' if stage == 'checking' else 'failed', timedelta(hours=retention_hours), run_id))
        await connection.execute("DELETE FROM uploads USING runs WHERE uploads.run_id=runs.run_id AND runs.state IN ('completed','completed_with_gaps','failed')")
        await connection.execute("DELETE FROM runs WHERE expires_at <= clock_timestamp() AND state IN ('completed','completed_with_gaps','failed')")


async def reserve_assessment(database_url, lease, sequence):
    async with locked(database_url, lease) as connection:
        cursor = await connection.execute(
            """UPDATE claim_checks SET assessment_attempts=assessment_attempts+1
               WHERE run_id=%s AND sequence=%s AND status='pending' AND assessment_attempts < 4
               RETURNING assessment_attempts""", (lease.run_id, sequence))
        return await cursor.fetchone() is not None
