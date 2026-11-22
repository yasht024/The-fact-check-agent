import json
import os
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.app.processing import assessment as a
from backend.app.processing.retrieval import Evidence
from backend.tests.test_search import fixture_claim


def source(passage="In 2024, solar energy production was 42 TWh.", period="2024"):
    return Evidence("https://energy.example/report", "Energy report", "2026-11-23T00:00:00+00:00",
                    None, period, passage, None)


def answer(outcome="supports", ids=None, correction=None):
    return {"outcome": outcome, "explanation": "The public report establishes this fact.",
            "evidence_ids": ["e1"] if ids is None else ids, "correction": correction}


class AssessmentTests(unittest.TestCase):
    def assess(self, response, *, evidence=None, context=None, claim=None):
        return a.assess_claim(claim or fixture_claim(), {"e1": source()} if evidence is None else evidence,
                              context or a.SearchContext(True, True), provider=lambda c, e: response)

    def test_verified_and_dated_historical_figure(self):
        evidence = {"e1": source(), "e2": source("In 2025, solar energy production was 56 TWh.", "2025")}
        result = self.assess(answer(), evidence=evidence)
        self.assertEqual(result.verdict, "verified")
        self.assertEqual(result.evidence_ids, ("e1",))

    def test_source_backed_correction(self):
        correction = {"text": "In 2024, solar energy production was 48 TWh.", "value": "48", "unit": "TWh",
                      "reporting_period": "2024", "evidence_id": "e1"}
        result = self.assess(answer("corrects", correction=correction), evidence={"e1": source(correction["text"])})
        self.assertEqual(result.verdict, "inaccurate")
        self.assertEqual(result.correction, correction)

    def test_newer_current_figure_can_correct(self):
        claim = fixture_claim().model_copy(update={"qualifiers": fixture_claim().qualifiers.model_copy(update={"reporting_period": "current"})})
        correction = {"text": "In 2025, solar energy production was 56 TWh.", "value": "56", "unit": "TWh",
                      "reporting_period": "2025", "evidence_id": "e1"}
        result = self.assess(answer("corrects", correction=correction), evidence={"e1": source(correction["text"], "2025")}, claim=claim)
        self.assertEqual(result.verdict, "inaccurate")

    def test_latest_completed_calendar_year_uses_current_period_rules(self):
        claim = fixture_claim().model_copy(update={"qualifiers": fixture_claim().qualifiers.model_copy(
            update={"reporting_period": "latest completed calendar year"})})
        correction = {"text": "In 2025, solar energy production was 56 TWh.", "value": "56", "unit": "TWh",
                      "reporting_period": "2025", "evidence_id": "e1"}
        result = self.assess(answer("corrects", correction=correction),
                             evidence={"e1": source(correction["text"], "2025")}, claim=claim)
        self.assertEqual(result.verdict, "inaccurate")
        self.assertFalse(a._is_current_period("current in 2024"))

    def test_calendar_year_label_matches_year_but_not_newer_or_fiscal_period(self):
        claim = fixture_claim().model_copy(update={"qualifiers": fixture_claim().qualifiers.model_copy(
            update={"reporting_period": "calendar year 2024"})})
        self.assertEqual(self.assess(answer(), claim=claim).verdict, "verified")
        newer = {"e1": source("In 2025, solar energy production was 56 TWh.", "2025")}
        self.assertIsNone(self.assess(answer("refutes"), claim=claim, evidence=newer).verdict)
        self.assertNotEqual(a._period_key("fiscal year 2024"), a._period_key("calendar year 2024"))

    def test_newer_period_does_not_refute_historical_figure(self):
        result = self.assess(answer("refutes"), evidence={"e1": source("In 2025, production was 56 TWh.", "2025")})
        self.assertIsNone(result.verdict)
        self.assertEqual(result.reason, "reporting_period_mismatch")

    def test_historical_correction_cannot_use_newer_period_even_with_old_citation(self):
        correction = {"text": "In 2025, solar energy production was 56 TWh.", "value": "56", "unit": "TWh",
                      "reporting_period": "2025", "evidence_id": "e2"}
        result = self.assess(answer("corrects", ids=["e1", "e2"], correction=correction),
                             evidence={"e1": source(), "e2": source(correction["text"], "2025")})
        self.assertIsNone(result.correction)
        self.assertIsNone(result.verdict)

    def test_refutation(self):
        self.assertEqual(self.assess(answer("refutes")).verdict, "false")

    def test_false_requires_completed_search_and_follow_up(self):
        self.assertEqual(self.assess(answer("unsupported", []), evidence={}).verdict, "false")
        self.assertEqual(self.assess(answer("unsupported")).verdict, "false")
        for context in (a.SearchContext(False, True), a.SearchContext(True, False),
                        a.SearchContext(True, True, "search_timeout"),
                        a.SearchContext(True, True, "material_source_unavailable")):
            result = self.assess(answer("unsupported", []), context=context, evidence={})
            self.assertEqual(result.status, "incomplete")
            self.assertIsNone(result.verdict)

    def test_conflict_unknown_id_and_missing_evidence_withheld(self):
        for response in (answer("unresolved"), answer(ids=["foreign-claim-evidence"]), answer(ids=[])):
            result = self.assess(response)
            self.assertIsNone(result.verdict)
            self.assertFalse(result.evidence_ids)
            self.assertIsNone(result.correction)

    def test_unsupported_corrections_are_withheld(self):
        valid = {"text": source().passage, "value": "42", "unit": "TWh", "reporting_period": "2024", "evidence_id": "e1"}
        for updates in ({"text": "Production was 99 TWh."}, {"value": "99"}, {"value": "2024"}, {"unit": "MWh"},
                        {"reporting_period": "2025"}, {"evidence_id": "unknown"}, {"value": None},
                        {"reporting_period": None}):
            result = self.assess(answer("corrects", correction={**valid, **updates}))
            self.assertIsNone(result.correction)
            self.assertIsNone(result.verdict)

    def test_provider_errors_invalid_and_incomplete_output(self):
        for response in ({}, {**answer(), "extra": 1}, {**answer(), "outcome": "verified"}):
            self.assertIsNone(self.assess(response).verdict)
        def failing(c, e):
            raise a.AssessmentError("assessment_timeout")
        result = a.assess_claim(fixture_claim(), {"e1": source()}, a.SearchContext(True), provider=failing)
        self.assertEqual(result.reason, "assessment_timeout")

    def test_links_are_built_from_evidence_only(self):
        response = answer()
        response["explanation"] = "See https://invented.example/unsupported"
        result = self.assess(response)
        self.assertNotIn("https://", result.explanation)

    def payload(self, response=None):
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps(answer() if response is None else response)}]}}]}

    def request(self, response):
        response.request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent")
        client = Mock()
        client.post.return_value = response
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}), patch.object(a.httpx, "Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = a.request_assessment(fixture_claim(), {"e1": source()})
        return result, client.post.call_args

    def test_gemini_api_contract_uses_shared_model_and_key(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-test-model"}):
            result, call = self.request(httpx.Response(200, json=self.payload()))
        self.assertEqual(result, answer())
        self.assertTrue(call.args[0].endswith('/gemini-test-model:generateContent'))
        self.assertEqual(call.kwargs['headers'], {'x-goog-api-key': 'test-only'})
        request = call.kwargs['json']
        config = request['generationConfig']
        self.assertEqual(config['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
        self.assertEqual(config['responseFormat']['text']['schema'], a.Assessment.model_json_schema())
        self.assertEqual(config['candidateCount'], 1)
        self.assertEqual(config['maxOutputTokens'], 2048)
        self.assertEqual(request['systemInstruction']['parts'][0]['text'], a.INSTRUCTIONS)
        self.assertNotIn('tools', request)

    def test_provider_adapter_rejects_failures(self):
        request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent")
        for response, code in ((httpx.Response(401, request=request), "assessment_credentials_rejected"),
                               (httpx.Response(429, request=request), "assessment_rate_limited"),
                               (httpx.Response(500, request=request), "assessment_provider_error"),
                               (httpx.Response(400, request=request, json={"error": {"details": [{
                                   "@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}), "assessment_credentials_rejected")):
            with self.assertRaises(a.AssessmentError) as raised:
                self.request(response)
            self.assertEqual(raised.exception.code, code)

    def test_gemini_response_guards(self):
        incomplete = self.payload()
        incomplete['candidates'][0]['finishReason'] = 'MAX_TOKENS'
        blocked = self.payload()
        blocked['candidates'][0]['finishReason'] = 'SAFETY'
        for payload, code in ((incomplete, 'assessment_incomplete_response'),
                              (blocked, 'assessment_refused'),
                              ({'promptFeedback': {'blockReason': 'SAFETY'}}, 'assessment_refused'),
                              ({}, 'assessment_invalid_response'),
                              (self.payload({'outcome': 'invented'}), 'assessment_invalid_response')):
            with self.assertRaises(a.AssessmentError) as raised:
                self.request(httpx.Response(200, json=payload))
            self.assertEqual(raised.exception.code, code)

    def test_gemini_ignores_thought_parts(self):
        payload = self.payload()
        payload['candidates'][0]['content']['parts'].insert(0, {'text': 'private reasoning', 'thought': True})
        result, _ = self.request(httpx.Response(200, json=payload))
        self.assertEqual(result, answer())

    def test_missing_gemini_key_and_invalid_model_do_not_send_requests(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'legacy-only'}, clear=True), patch.object(a.httpx, 'Client') as client:
            with self.assertRaises(a.AssessmentError) as raised:
                a.request_assessment(fixture_claim(), {'e1': source()})
            self.assertEqual(raised.exception.code, 'assessment_key_missing')
            client.assert_not_called()
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only', 'GEMINI_MODEL': '../invalid'}), patch.object(a.httpx, 'Client') as client:
            with self.assertRaises(a.AssessmentError) as raised:
                a.request_assessment(fixture_claim(), {'e1': source()})
            self.assertEqual(raised.exception.code, 'assessment_invalid_model_configuration')
            client.assert_not_called()

    def test_gemini_timeout_is_incomplete(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'}), patch.object(a.httpx, 'Client') as factory:
            factory.return_value.__enter__.return_value.post.side_effect = httpx.ReadTimeout('timeout')
            result = a.assess_claim(fixture_claim(), {'e1': source()}, a.SearchContext(True))
        self.assertIsNone(result.verdict)
        self.assertEqual(result.reason, 'assessment_timeout')
