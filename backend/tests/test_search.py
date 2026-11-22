import os
import json
import unittest
from unittest.mock import patch

import httpx

from backend.app.processing.claims import Claim, Qualifiers
from backend.app.processing.search import SearchError, build_query, identify_primary_domain, search_sources


def fixture_claim():
    return Claim(excerpt="Solar energy production was 42 TWh in 2024.", page_number=1,
                 check_target="Solar energy production was 42 TWh in 2024",
                 qualifiers=Qualifiers(units="TWh", geography="Exampleland", population=None,
                                       reporting_period="2024", other=None))


class SearchTests(unittest.TestCase):
    def request(self, handler):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-only"}), httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return search_sources("solar energy 2024", client=client)

    def test_queries(self):
        initial = build_query(fixture_claim())
        follow_up = build_query(fixture_claim(), follow_up=True, primary_domain="energy.gov")
        self.assertIn("42", initial)
        self.assertNotIn("42", follow_up)
        for text in ("2024", "Exampleland", "TWh", "site:energy.gov"):
            self.assertIn(text, follow_up)
        self.assertNotEqual(initial, follow_up)

    def test_follow_up_identifies_named_publisher(self):
        claim = fixture_claim().model_copy(update={"check_target": "NASA solar energy was 42 TWh"})
        self.assertIn("site:nasa.gov", build_query(claim, follow_up=True))

    def test_explicit_primary_domain_can_target_initial_search(self):
        claim = fixture_claim().model_copy(update={"check_target": "Amazon annual net sales"})
        domain = identify_primary_domain(claim)
        self.assertEqual(domain, "ir.aboutamazon.com")
        self.assertIn("site:ir.aboutamazon.com", build_query(claim, primary_domain=domain))
        self.assertNotIn("site:", build_query(claim, follow_up=True, primary_domain=""))

    def test_tavily_domain_filter_uses_include_domains(self):
        def handler(request):
            import json
            body = json.loads(request.content)
            self.assertEqual(body['include_domains'], ['ir.aboutamazon.com'])
            self.assertNotIn('site:', body['query'])
            return httpx.Response(200, json={'results': []})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test-only'}), httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = search_sources('Amazon annual results site:ir.aboutamazon.com', client=client)
        self.assertEqual(result.candidates, ())

    def test_success(self):
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-only")
            self.assertEqual(request.method, "POST")
            self.assertEqual(str(request.url), "https://api.tavily.com/search")
            self.assertEqual(json.loads(request.content)["max_results"], 10)
            return httpx.Response(200, json={"results": [
                {"url": "https://energy.gov/report", "title": "Report"},
                {"url": "https://energy.gov/report", "title": "Duplicate"}]})
        self.assertEqual(len(self.request(handler).candidates), 1)

    def test_empty_success(self):
        for payload in ({"results": []}, {"query": "solar energy", "results": []}):
            self.assertEqual(self.request(lambda r: httpx.Response(200, json=payload)).candidates, ())

    def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        with self.assertRaises(SearchError) as raised:
            self.request(handler)
        self.assertEqual(raised.exception.code, "search_timeout")

    def test_provider_errors(self):
        for status, code in ((500, "search_provider_error"), (429, "search_rate_limited"),
                             (401, "search_credentials_rejected"), (403, "search_credentials_rejected"),
                             (432, "search_quota_exceeded"), (433, "search_quota_exceeded")):
            with self.subTest(status=status), self.assertRaises(SearchError) as raised:
                self.request(lambda r: httpx.Response(status))
            self.assertEqual(raised.exception.code, code)

    def test_malformed_is_not_empty_success(self):
        for payload in ({}, {"results": None}, {"error": "bad"}):
            with self.assertRaises(SearchError) as raised:
                self.request(lambda r: httpx.Response(200, json=payload))
            self.assertEqual(raised.exception.code, "search_invalid_response")

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SearchError) as raised:
            search_sources("test")
        self.assertEqual(raised.exception.code, "search_key_missing")
