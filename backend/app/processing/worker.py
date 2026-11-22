import asyncio
import argparse
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

import psycopg

from backend.app.config import Settings
from backend.app.processing import job_queue
from backend.app.processing import pipeline, pipeline_store
from backend.app.processing.job_queue import RunLease
from backend.app.processing.claims import Claim
from backend.app.processing.search import SearchError, build_query, search_sources


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
    processor: Processor | None = None,
    providers: pipeline.Providers | None = None,
    *,
    lease_duration: timedelta = job_queue.LEASE_DURATION,
) -> bool:
    if processor is None:
        settings = Settings.from_env()
        await pipeline_store.cleanup(database_url, settings.processing_deadline_seconds, settings.report_retention_hours)
    lease = await job_queue.claim_run(database_url, lease_duration)
    if lease is None:
        return False

    processing = asyncio.create_task(
        _process_lease(database_url, lease, processor) if processor is not None
        else pipeline.process_pipeline(database_url, lease, providers)
    )
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


async def discover_sources(claim: Claim, *, client=None):
    """Search outside the event loop so a queued processor can keep renewing its lease."""
    return await asyncio.to_thread(search_sources, build_query(claim), client=client)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the worker or search sources for a claim.")
    parser.add_argument("--search-claim", type=Path, help="Search a Claim JSON file and return candidate URLs.")
    arguments = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from None

    if arguments.search_claim is not None:
        try:
            claim = Claim.model_validate_json(arguments.search_claim.read_text(encoding="utf-8"))
            result = asyncio.run(discover_sources(claim))
        except (OSError, ValidationError, ValueError):
            print(json.dumps({"status": "error", "code": "invalid_claim"}))
            return 1
        except SearchError as error:
            print(json.dumps({"status": "error", "code": error.code}))
            return 1
        print(json.dumps({"status": "completed" if result.candidates else "empty",
                          "candidates": [asdict(candidate) for candidate in result.candidates]}))
        return 0

    if settings.database_url is None:
        print("Worker configuration loaded. Set DATABASE_URL to process queued runs.")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Provider request URLs can include search text; keep HTTP client logs out of worker output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run_worker(settings.database_url))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
