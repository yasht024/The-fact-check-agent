import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import psycopg

from backend.app.config import Settings
from backend.app.processing import job_queue
from backend.app.processing.job_queue import RunLease


logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 1
Processor = Callable[[RunLease, str], Awaitable[None]]


class RetryableProcessingError(Exception):
    """A processing error that can succeed on a later attempt."""


async def simulate_step(lease: RunLease, step: str) -> None:
    logger.info("Simulating %s for run %s (attempt %s)", step, lease.run_id, lease.attempt_count)
    await asyncio.sleep(0.1)


async def _process_lease(database_url: str, lease: RunLease, processor: Processor) -> None:
    steps = (
        (job_queue.CHECKPOINT_STAGE, job_queue.save_checkpoint),
        ("completed", job_queue.complete_run),
    )
    if lease.stage == job_queue.CHECKPOINT_STAGE:
        steps = steps[1:]

    for step, save in steps:
        try:
            await processor(lease, step)
        except RetryableProcessingError:
            if await job_queue.retry_run(database_url, lease):
                logger.info("Recorded retryable failure for run %s (attempt %s)", lease.run_id, lease.attempt_count)
            return
        except Exception:
            if await job_queue.fail_run(database_url, lease, "processor_failed"):
                logger.error("Processor failed for run %s", lease.run_id)
            return

        if not await save(database_url, lease):
            return


async def process_one(
    database_url: str,
    processor: Processor = simulate_step,
    *,
    lease_duration: timedelta = job_queue.LEASE_DURATION,
) -> bool:
    lease = await job_queue.claim_run(database_url, lease_duration)
    if lease is None:
        return False

    processing = asyncio.create_task(_process_lease(database_url, lease, processor))
    try:
        while True:
            finished, _ = await asyncio.wait((processing,), timeout=lease_duration.total_seconds() / 3)
            if finished:
                await processing
                return True
            if not await job_queue.renew_lease(database_url, lease, lease_duration):
                return True
    finally:
        if not processing.done():
            processing.cancel()
        await asyncio.gather(processing, return_exceptions=True)


async def run_worker(database_url: str) -> None:
    while True:
        try:
            if await process_one(database_url):
                continue
        except psycopg.Error as error:
            logger.warning("Worker database operation failed (SQLSTATE %s)", error.sqlstate)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from None

    if settings.database_url is None:
        print("Worker configuration loaded. Set DATABASE_URL to process queued runs.")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run_worker(settings.database_url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
