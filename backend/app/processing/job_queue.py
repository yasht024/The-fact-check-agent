from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
from psycopg import sql


LEASE_DURATION = timedelta(seconds=30)
MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY = timedelta(seconds=5)
CHECKPOINT_STAGE = "simulated_checkpoint"


@dataclass(frozen=True)
class RunLease:
    run_id: UUID
    lease_id: UUID = field(repr=False)
    stage: str
    attempt_count: int


async def claim_run(database_url: str, duration: timedelta = LEASE_DURATION) -> RunLease | None:
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        cursor = await connection.execute(
            """
            SELECT run_id, stage, attempt_count
            FROM runs
            WHERE (state = 'queued' AND next_attempt_at <= clock_timestamp())
               OR (state = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp()))
            ORDER BY created_at, run_id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
        candidate = await cursor.fetchone()
        if candidate is None:
            return None

        run_id, stage, attempt_count = candidate
        if attempt_count >= MAX_ATTEMPTS:
            await connection.execute(
                """
                UPDATE runs
                SET state = 'failed', failure_code = 'attempts_exhausted',
                    lease_id = NULL, lease_expires_at = NULL
                WHERE run_id = %s
                """,
                (run_id,),
            )
            return None

        lease = RunLease(run_id, uuid4(), stage, attempt_count + 1)
        await connection.execute(
            """
            UPDATE runs
            SET state = 'running', lease_id = %s, lease_expires_at = clock_timestamp() + %s,
                attempt_count = %s
            WHERE run_id = %s
            """,
            (lease.lease_id, duration, lease.attempt_count, run_id),
        )
        return lease


async def _update_leased_run(
    database_url: str,
    lease: RunLease,
    changes: sql.SQL,
    values: tuple[object, ...] = (),
    *,
    expected_stage: str | None = None,
) -> bool:
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        cursor = await connection.execute(
            """
            SELECT stage FROM runs
            WHERE run_id = %s AND lease_id = %s AND state = 'running'
            FOR UPDATE
            """,
            (lease.run_id, lease.lease_id),
        )
        row = await cursor.fetchone()
        if row is None or (expected_stage is not None and row[0] != expected_stage):
            return False

        # Check the database clock after acquiring the row lock. The lease may
        # have expired while this transaction was waiting for the lock.
        cursor = await connection.execute(
            sql.SQL(
                "UPDATE runs SET {} WHERE run_id = %s AND lease_expires_at > clock_timestamp()"
            ).format(changes),
            (*values, lease.run_id),
        )
        return cursor.rowcount == 1


async def renew_lease(database_url: str, lease: RunLease, duration: timedelta = LEASE_DURATION) -> bool:
    return await _update_leased_run(
        database_url, lease, sql.SQL("lease_expires_at = clock_timestamp() + %s"), (duration,)
    )


async def save_checkpoint(database_url: str, lease: RunLease) -> bool:
    return await _update_leased_run(
        database_url, lease, sql.SQL("stage = %s"), (CHECKPOINT_STAGE,), expected_stage="queued"
    )


async def complete_run(database_url: str, lease: RunLease) -> bool:
    return await _update_leased_run(
        database_url,
        lease,
        sql.SQL("state = 'completed', stage = 'completed', lease_id = NULL, lease_expires_at = NULL"),
        expected_stage=CHECKPOINT_STAGE,
    )


async def fail_run(database_url: str, lease: RunLease, failure_code: str) -> bool:
    return await _update_leased_run(
        database_url,
        lease,
        sql.SQL("state = 'failed', failure_code = %s, lease_id = NULL, lease_expires_at = NULL"),
        (failure_code,),
    )


async def retry_run(database_url: str, lease: RunLease) -> bool:
    if lease.attempt_count >= MAX_ATTEMPTS:
        return await fail_run(database_url, lease, "attempts_exhausted")

    delay = FIRST_RETRY_DELAY * (2 ** (lease.attempt_count - 1))
    return await _update_leased_run(
        database_url,
        lease,
        sql.SQL(
            "state = 'queued', next_attempt_at = clock_timestamp() + %s, "
            "lease_id = NULL, lease_expires_at = NULL"
        ),
        (delay,),
    )
