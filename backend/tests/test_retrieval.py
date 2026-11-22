import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.processing import retrieval as r
from backend.app.processing.search import Candidate
from backend.tests.test_search import fixture_claim

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATE = Candidate("https://public.example/report", "Search title")


class RetrievalTests(unittest.TestCase):
    def document(self, name, content_type):
        return r.SourceDocument("https://public.example/final", (FIXTURES / name).read_bytes(), content_type, "2026-11-23T00:00:00+00:00")

    def test_saved_html(self):
        evidence = r.retrieve_evidence(CANDIDATE, fixture_claim(), fetcher=lambda u: self.document("source.html", "text/html"))
        self.assertTrue(evidence)
        self.assertEqual(evidence[0].title, "Public energy report")
        self.assertEqual(evidence[0].source_url, "https://public.example/final")
        self.assertEqual(evidence[0].reporting_period, "2024")
        self.assertEqual(evidence[0].publication_date, "2025-02-01")
        self.assertIn("42 TWh", evidence[0].passage)
        self.assertNotIn("999", " ".join(e.passage for e in evidence))

    def test_saved_pdf(self):
        evidence = r.retrieve_evidence(CANDIDATE, fixture_claim(), fetcher=lambda u: self.document("source.pdf", "application/pdf"))
        self.assertEqual(evidence[0].page_number, 1)
        self.assertEqual(evidence[0].title, "Energy annual report 2024")
        self.assertEqual(evidence[0].reporting_period, "2024")
        self.assertIn("42 TWh", evidence[0].passage)

    def connection(self, response):
        connection = Mock()
        connection.getresponse.return_value = response
        return connection

    def response(self, status=200, headers=None, chunks=None):
        response = Mock(status=status)
        headers = headers or {}
        response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
        response.read1.side_effect = chunks or [b"ok", b""]
        return response

    def test_falls_back_to_another_validated_address_after_transport_failure(self):
        first = self.connection(self.response())
        first.request.side_effect = OSError('Network unreachable')
        second = self.connection(self.response())
        with patch.object(r, '_public_addresses', return_value=['8.8.8.8', '8.8.4.4']), patch.object(r, '_PinnedHTTPS', side_effect=[first, second]):
            document = r.fetch_source(CANDIDATE.url)
        self.assertEqual(document.body, b'ok')
        first.close.assert_called_once()
        self.assertEqual(second.pinned_address, '8.8.4.4')

    def test_address_fallback_shares_the_original_deadline(self):
        first = self.connection(self.response())
        first.request.side_effect = TimeoutError()
        with patch.object(r, '_public_addresses', return_value=['8.8.8.8', '8.8.4.4']), patch.object(r, '_PinnedHTTPS', return_value=first) as connect, patch.object(r.time, 'monotonic', side_effect=[0, 0, 0, 16]), self.assertRaises(r.RetrievalError) as raised:
            r.fetch_source(CANDIDATE.url)
        self.assertEqual(raised.exception.code, 'source_timeout')
        self.assertEqual(connect.call_count, 1)

    def test_prefers_ipv4_when_cloud_has_no_ipv6_egress(self):
        connection = self.connection(self.response())
        with patch.object(r, '_public_addresses', return_value=['2606:4700:4700::1111', '8.8.8.8']), patch.object(r, '_PinnedHTTPS', return_value=connection):
            r.fetch_source(CANDIDATE.url)
        self.assertEqual(connection.pinned_address, '8.8.8.8')

    def test_unavailable(self):
        with patch.object(r, "_public_addresses", return_value=["8.8.8.8"]), patch.object(r, "_PinnedHTTPS", return_value=self.connection(self.response(404))), self.assertRaises(r.RetrievalError) as raised:
            r.retrieve_evidence(CANDIDATE, fixture_claim())
        self.assertEqual(raised.exception.code, "source_unavailable")

    def test_oversized_declared_and_streamed(self):
        for response in (self.response(headers={"Content-Length": "10"}), self.response(chunks=[b"123456"])):
            with patch.object(r, "_public_addresses", return_value=["8.8.8.8"]), patch.object(r, "_PinnedHTTPS", return_value=self.connection(response)), self.assertRaises(r.RetrievalError) as raised:
                r.fetch_source(CANDIDATE.url, max_bytes=5)
            self.assertEqual(raised.exception.code, "source_too_large")

    def test_redirect_to_private_address(self):
        response = self.response(302, {"Location": "http://127.0.0.1/private"})
        def dns(host, port, timeout):
            addresses = ["127.0.0.1"] if host == "127.0.0.1" else ["8.8.8.8"]
            return subprocess.CompletedProcess([], 0, json.dumps(addresses).encode())
        with patch.object(r.subprocess, "run", side_effect=dns_adapter(dns)), patch.object(r, "_PinnedHTTPS", return_value=self.connection(response)), patch.object(r, "_PinnedHTTP") as private, self.assertRaises(r.RetrievalError) as raised:
            r.fetch_source(CANDIDATE.url)
        self.assertEqual(raised.exception.code, "source_private_address")
        private.assert_not_called()

    def test_private_dns_answers(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"):
            with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, f'["8.8.8.8", "{address}"]'.encode())), self.assertRaises(r.RetrievalError):
                r._public_addresses("example.org", 443, 1)

    def test_connection_pins_validated_address(self):
        connection = r._PinnedHTTP("example.org", 80)
        connection.pinned_address = "8.8.8.8"
        with patch.object(r.socket, "create_connection") as connect:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 80))

    def test_tls_uses_original_hostname(self):
        connection = r._PinnedHTTPS("example.org", 443)
        connection.pinned_address = "8.8.8.8"
        with patch.object(r.socket, "create_connection") as connect, patch.object(r.ssl, "create_default_context") as context:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ("8.8.8.8", 443))
        self.assertEqual(context.return_value.wrap_socket.call_args.kwargs["server_hostname"], "example.org")

    def test_timeout_and_redirect_limit(self):
        with patch.object(r, "_public_addresses", side_effect=r.RetrievalError("source_timeout")), self.assertRaises(r.RetrievalError) as raised:
            r.fetch_source(CANDIDATE.url)
        self.assertEqual(raised.exception.code, "source_timeout")
        with patch.object(r, "_public_addresses", return_value=["8.8.8.8"]), patch.object(r, "_PinnedHTTPS", return_value=self.connection(self.response(302, {"Location": "/again"}))), self.assertRaises(r.RetrievalError) as raised:
            r.fetch_source(CANDIDATE.url)
        self.assertEqual(raised.exception.code, "source_redirect_limit")

    def test_no_relevance_and_unsupported_type(self):
        document = r.SourceDocument(CANDIDATE.url, b"<p>A museum opened.</p>", "text/html", "now")
        self.assertEqual(r.retrieve_evidence(CANDIDATE, fixture_claim(), fetcher=lambda u: document), ())
        with self.assertRaises(r.RetrievalError):
            r.retrieve_evidence(CANDIDATE, fixture_claim(), fetcher=lambda u: r.SourceDocument(CANDIDATE.url, b"data", "image/png", "now"))


def dns_adapter(function):
    return lambda args, **kwargs: function(args[-2], int(args[-1]), kwargs["timeout"])


class NumericPassageRankingTests(unittest.TestCase):
    def test_value_and_year_rank_specific_passage_above_generic_words(self):
        claim = fixture_claim().model_copy(update={
            'excerpt': 'Amazon full-year net sales were 638.0 billion in 2024.',
            'check_target': 'Amazon full-year net sales were 638.0 billion in 2024',
            'qualifiers': fixture_claim().qualifiers.model_copy(update={
                'units': 'billion', 'geography': None, 'population': 'Amazon', 'reporting_period': '2024'})})
        body = b'<title>Annual report</title><p>Amazon full-year net sales billion customer news.</p><p>Net sales reached $638.0 billion in 2024.</p>'
        document = r.SourceDocument(CANDIDATE.url, body, 'text/html', '2026-11-23T00:00:00+00:00')
        evidence = r.retrieve_evidence(CANDIDATE, claim, fetcher=lambda u: document)
        self.assertIn('638.0', evidence[0].passage)
        self.assertEqual(evidence[0].reporting_period, '2024')
