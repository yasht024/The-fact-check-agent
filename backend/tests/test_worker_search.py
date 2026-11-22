import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from backend.app.processing import worker
from backend.app.processing import pipeline
from backend.app.processing.claims import Claim
from backend.app.processing.assessment import Finding
from backend.app.config import Settings


FIXTURE = Path(__file__).parent / "fixtures" / "known_claim.json"


class WorkerSearchTests(unittest.TestCase):
    def run_worker_search(self, handler):
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport_client, \
                patch.dict(os.environ, {"TAVILY_API_KEY": "test-only"}), \
                patch("backend.app.processing.search.httpx.Client") as client:
            client.return_value.__enter__.return_value = transport_client
            with redirect_stdout(io.StringIO()) as output:
                code = worker.main(["--search-claim", str(FIXTURE)])
        return code, json.loads(output.getvalue())

    def test_known_claim_returns_urls_through_worker(self):
        def handler(request):
            self.assertIn("12,756", json.loads(request.content)["query"])
            return httpx.Response(200, json={"results": [
                {"url": "https://science.nasa.gov/earth/facts/", "title": "Earth facts"}]})
        code, result = self.run_worker_search(handler)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"][0]["url"], "https://science.nasa.gov/earth/facts/")

    def test_empty_is_success_through_worker(self):
        code, result = self.run_worker_search(lambda request: httpx.Response(200, json={"results": []}))
        self.assertEqual((code, result), (0, {"status": "empty", "candidates": []}))

    def test_timeout_is_distinct_through_worker(self):
        def handler(request):
            raise httpx.ReadTimeout("test timeout", request=request)
        code, result = self.run_worker_search(handler)
        self.assertEqual((code, result), (1, {"status": "error", "code": "search_timeout"}))

    def test_authentication_failure_is_distinct_through_worker(self):
        code, result = self.run_worker_search(lambda request: httpx.Response(401))
        self.assertEqual((code, result), (1, {"status": "error", "code": "search_credentials_rejected"}))


class PipelineSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_pipeline_search_passes_tavily_urls_to_retrieval(self):
        def handler(request):
            return httpx.Response(200, json={"results": [
                {"url": "https://science.nasa.gov/earth/facts/", "title": "Earth facts"}]})

        retrieve = Mock(return_value=[])
        assess = Mock(return_value=Finding("completed", "false", "Test finding"))
        providers = pipeline.Providers(retrieve=retrieve, assess=assess)
        claim = Claim.model_validate_json(FIXTURE.read_text())
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport_client, \
                patch.dict(os.environ, {"TAVILY_API_KEY": "test-only"}), \
                patch("backend.app.processing.search.httpx.Client") as client, \
                patch.object(pipeline.store, "reserve_search", new=AsyncMock(return_value=1)), \
                patch.object(pipeline.store, "record_search", new=AsyncMock()) as record:
            client.return_value.__enter__.return_value = transport_client
            await pipeline._check("test-database", Mock(run_id="test-run"), 1, claim, providers, Settings.from_env())

        self.assertEqual(retrieve.call_args.args[0].url, "https://science.nasa.gov/earth/facts/")
        self.assertEqual(record.call_args.args[-1], "completed")
