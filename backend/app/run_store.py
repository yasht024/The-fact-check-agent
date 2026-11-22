from uuid import UUID
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row


async def create_run(database_url: str, run_id: UUID, token_hash: bytes, pdf: bytes) -> None:
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        await connection.execute(
            "INSERT INTO runs (run_id, access_token_hash) VALUES (%s, %s)",
            (run_id, token_hash),
        )
        await connection.execute(
            "INSERT INTO uploads (run_id, pdf) VALUES (%s, %s)",
            (run_id, pdf),
        )


async def get_run(database_url: str, run_id: UUID) -> tuple[str, str, bytes] | None:
    async with await psycopg.AsyncConnection.connect(
        database_url, connect_timeout=5, autocommit=True
    ) as connection:
        cursor = await connection.execute(
            "SELECT state, stage, access_token_hash FROM runs WHERE run_id = %s",
            (run_id,),
        )
        return await cursor.fetchone()


async def get_report_details(database_url: str, run_id: UUID) -> dict | None:
    """Read a consistent report snapshot after the route validates its token."""
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5, row_factory=dict_row) as connection:
        await connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor = await connection.execute(
            "SELECT state,stage,failure_code,coverage_gaps,expires_at FROM runs WHERE run_id=%s", (run_id,))
        run = await cursor.fetchone()
        if run is None or (run['expires_at'] and run['expires_at'] <= datetime.now(timezone.utc)):
            return None
        cursor = await connection.execute(
            """SELECT c.sequence,c.claim,k.status,k.verdict,k.explanation,k.reason,k.correction,
               k.evidence_ids,k.search_attempts,k.assessment_attempts,k.source_failures FROM claims c
               JOIN claim_checks k USING(run_id,sequence) WHERE c.run_id=%s ORDER BY c.sequence""", (run_id,))
        claims = await cursor.fetchall()
        cursor = await connection.execute(
            """SELECT sequence,evidence_id,source_url,title,retrieved_at,publication_date,reporting_period,
               passage,page_number FROM evidence WHERE run_id=%s ORDER BY sequence,evidence_id""", (run_id,))
        evidence = await cursor.fetchall()
        for item in claims:
            item['evidence'] = [source for source in evidence
                                if source['sequence'] == item['sequence'] and source['evidence_id'] in item['evidence_ids']]
        return {'state': run['state'], 'stage': run['stage'], 'failure_code': run['failure_code'],
                'coverage_gaps': run['coverage_gaps'], 'claims': claims,
                'progress': {'total': len(claims), 'checked': sum(item['status'] != 'pending' for item in claims)}}
