"""Public UI limits follow server settings without exposing credentials."""
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app

class InterfaceConfigTests(unittest.TestCase):
    def test_public_limits_follow_environment(self):
        with patch.dict(os.environ, {'MAX_UPLOAD_BYTES': '2048', 'MAX_PDF_PAGES': '2', 'REPORT_RETENTION_HOURS': '3', 'GEMINI_API_KEY': 'must-not-be-exposed'}):
            response = TestClient(app).get('/api/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'max_upload_bytes': 2048, 'max_pdf_pages': 2, 'report_retention_hours': 3})
