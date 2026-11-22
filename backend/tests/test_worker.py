import asyncio
import os
import unittest
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app import run_store
from backend.app.processing import job_queue, worker


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "Set TEST_DATABASE_URL to run the worker acceptance checks.")
class WorkerPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        schema_name = f"phase3_test_{uuid4().hex}"
        schema = sql.Identifier(schema_name)
        admin = await self.enterAsyncContext(
            await psycopg.AsyncConnection.connect(TEST_DATABASE_URL, autocommit=True)
        )
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        self.addAsyncCleanup(admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
        self.database_url = make_conninfo(TEST_DATABASE_URL, options=f"-csearch_path={schema_name}")
        self.database = await self.enterAsyncContext(
            await psycopg.AsyncConnection.connect(self.database_url, autocommit=True)
        )

        # Create the queued run under the Phase 2 schema to exercise upgrading it.
        await self.apply_migration("001_runs_and_uploads.sql")
        self.run_id = await self.enqueue()
        await self.apply_migration("002_worker_leases.sql")

    async def apply_migration(self, name: str) -> None:
        await self.database.execute((MIGRATIONS / name).read_text(encoding="utf-8"))

    async def enqueue(self) -> UUID:
        run_id = uuid4()
        await run_store.create_run(self.database_url, run_id, bytes(32), b"%PDF-1.4\n")
        return run_id

    async def run_state(self) -> tuple:
        cursor = await self.database.execute(
            """
            SELECT state, stage, attempt_count, lease_id, lease_expires_at, failure_code
            FROM runs WHERE run_id = %s
            """,
            (self.run_id,),
        )
        return await cursor.fetchone()

    async def expire_lease(self) -> None:
        await self.database.execute(
            "UPDATE runs SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE run_id = %s",
            (self.run_id,),
        )

    async def test_only_one_worker_claims_a_queued_run(self) -> None:
        claims = await asyncio.gather(
            job_queue.claim_run(self.database_url), job_queue.claim_run(self.database_url)
        )
        self.assertEqual(claims.count(None), 1)
        lease = next(claim for claim in claims if claim is not None)
        self.assertEqual(lease.run_id, self.run_id)
        self.assertEqual((await self.run_state())[:4], ("running", "queued", 1, lease.lease_id))

    async def test_claim_skips_a_row_locked_by_another_worker(self) -> None:
        other_run = await self.enqueue()
        async with await psycopg.AsyncConnection.connect(self.database_url) as locking_connection:
            await locking_connection.execute("SELECT 1 FROM runs WHERE run_id = %s FOR UPDATE", (self.run_id,))
            lease = await asyncio.wait_for(job_queue.claim_run(self.database_url), timeout=5)
            self.assertEqual(lease.run_id, other_run)
            self.assertEqual((await self.run_state())[:3], ("queued", "queued", 0))

    async def test_replacement_resumes_checkpoint_and_rejects_stale_writes(self) -> None:
        checkpoint_saved = asyncio.Event()
        original_claims = []

        async def interrupted_processor(lease: job_queue.RunLease, step: str) -> None:
            if step == "completed":
                original_claims.append(lease)
                checkpoint_saved.set()
                await asyncio.Future()

        processing = asyncio.create_task(worker.process_one(self.database_url, interrupted_processor))
        try:
            await asyncio.wait_for(checkpoint_saved.wait(), timeout=5)
            original = original_claims[0]
            self.assertEqual(
                (await self.run_state())[:4],
                ("running", job_queue.CHECKPOINT_STAGE, 1, original.lease_id),
            )
            self.assertIsNone(await job_queue.claim_run(self.database_url))
        finally:
            processing.cancel()
            with suppress(asyncio.CancelledError):
                await processing

        self.assertIsNone(await job_queue.claim_run(self.database_url))
        await self.expire_lease()
        self.assertFalse(await job_queue.renew_lease(self.database_url, original))
        self.assertFalse(await job_queue.complete_run(self.database_url, original))

        steps = []
        stale_results = []

        async def replacement_processor(lease: job_queue.RunLease, step: str) -> None:
            steps.append((lease.lease_id, lease.attempt_count, lease.stage, step))
            stale_results.append(await job_queue.renew_lease(self.database_url, original))
            stale_results.append(await job_queue.retry_run(self.database_url, original))
            stale_results.append(await job_queue.complete_run(self.database_url, original))

        self.assertTrue(await worker.process_one(self.database_url, replacement_processor))
        self.assertEqual(len(steps), 1)
        replacement_id, attempt, stage, step = steps[0]
        self.assertNotEqual(replacement_id, original.lease_id)
        self.assertEqual((attempt, stage, step), (2, job_queue.CHECKPOINT_STAGE, "completed"))
        self.assertEqual(stale_results, [False, False, False])
        self.assertEqual(await self.run_state(), ("completed", "completed", 2, None, None, None))

    async def test_live_worker_renews_its_lease(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_processor(lease: job_queue.RunLease, step: str) -> None:
            if step == job_queue.CHECKPOINT_STAGE:
                started.set()
                await release.wait()

        processing = asyncio.create_task(
            worker.process_one(self.database_url, slow_processor, lease_duration=timedelta(seconds=6))
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            initial_expiry = (await self.run_state())[4]

            async def wait_for_renewal() -> None:
                while (await self.run_state())[4] <= initial_expiry:
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(wait_for_renewal(), timeout=8)
            self.assertIsNone(await job_queue.claim_run(self.database_url))
            release.set()
            self.assertTrue(await asyncio.wait_for(processing, timeout=5))
        finally:
            if not processing.done():
                processing.cancel()
                with suppress(asyncio.CancelledError):
                    await processing
        self.assertEqual(await self.run_state(), ("completed", "completed", 1, None, None, None))

    async def test_retries_keep_checkpoint_and_eventually_fail(self) -> None:
        steps = []

        async def retryable_processor(lease: job_queue.RunLease, step: str) -> None:
            steps.append((lease.attempt_count, step))
            if step == "completed":
                raise worker.RetryableProcessingError

        for attempt in range(1, job_queue.MAX_ATTEMPTS + 1):
            self.assertTrue(await worker.process_one(self.database_url, retryable_processor))
            if attempt < job_queue.MAX_ATTEMPTS:
                self.assertEqual(
                    await self.run_state(), ("queued", job_queue.CHECKPOINT_STAGE, attempt, None, None, None)
                )
                self.assertIsNone(await job_queue.claim_run(self.database_url))
                await self.database.execute(
                    "UPDATE runs SET next_attempt_at = clock_timestamp() WHERE run_id = %s", (self.run_id,)
                )

        self.assertEqual(
            steps,
            [(1, job_queue.CHECKPOINT_STAGE)]
            + [(attempt, "completed") for attempt in range(1, job_queue.MAX_ATTEMPTS + 1)],
        )
        self.assertEqual(
            await self.run_state(),
            ("failed", job_queue.CHECKPOINT_STAGE, job_queue.MAX_ATTEMPTS, None, None, "attempts_exhausted"),
        )
        self.assertIsNone(await job_queue.claim_run(self.database_url))

    async def test_expired_final_attempt_is_closed_and_stale_progress_is_rejected(self) -> None:
        previous_lease = None
        for attempt in range(1, job_queue.MAX_ATTEMPTS + 1):
            lease = await job_queue.claim_run(self.database_url)
            self.assertEqual(lease.attempt_count, attempt)
            if previous_lease is not None:
                self.assertFalse(await job_queue.save_checkpoint(self.database_url, previous_lease))
            await self.expire_lease()
            self.assertFalse(await job_queue.save_checkpoint(self.database_url, lease))
            previous_lease = lease

        self.assertIsNone(await job_queue.claim_run(self.database_url))
        self.assertEqual(
            await self.run_state(), ("failed", "queued", job_queue.MAX_ATTEMPTS, None, None, "attempts_exhausted")
        )

    async def test_unexpected_processor_error_fails_without_retrying(self) -> None:
        async def broken_processor(lease: job_queue.RunLease, step: str) -> None:
            raise RuntimeError

        with self.assertLogs(worker.logger, level="ERROR"):
            self.assertTrue(await worker.process_one(self.database_url, broken_processor))
        self.assertEqual(await self.run_state(), ("failed", "queued", 1, None, None, "processor_failed"))
        self.assertIsNone(await job_queue.claim_run(self.database_url))
