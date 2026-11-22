import os
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app import run_store
from backend.app.main import app


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "001_runs_and_uploads.sql"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def small_pdf() -> bytes:
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources <<>> >>",
    )
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Root 1 0 R /Size 4 >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(pdf)


class UploadValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = await self.enterAsyncContext(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        )
        self.pdf = small_pdf()
        self.enterContext(patch.dict(os.environ, {"MAX_UPLOAD_BYTES": str(len(self.pdf))}))
        self.create_run = self.enterContext(patch.object(run_store, "create_run"))

    async def test_rejects_non_pdf_even_with_pdf_name_and_content_type(self) -> None:
        response = await self.client.post(
            "/api/runs", files={"file": ("test.pdf", b"not a PDF", "application/pdf")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_pdf")
        self.create_run.assert_not_awaited()

    async def test_requires_exactly_one_file_named_file(self) -> None:
        file = ("test.pdf", self.pdf, "application/pdf")
        cases = {
            "missing file": None,
            "wrong field": [("pdf", file)],
            "multiple files": [("file", file), ("file", file)],
        }
        for case, files in cases.items():
            with self.subTest(case=case):
                response = await self.client.post("/api/runs", files=files)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_pdf")
        self.create_run.assert_not_awaited()

    async def test_rejects_file_one_byte_over_limit(self) -> None:
        response = await self.client.post(
            "/api/runs", files={"file": ("test.pdf", self.pdf + b"\n", "application/pdf")}
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "upload_too_large")
        self.create_run.assert_not_awaited()

    async def test_rejects_oversized_stream_without_content_length(self) -> None:
        async def body():
            yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\n\r\n'
            yield self.pdf
            for _ in range(3):
                yield b" " * (32 * 1024)
            yield b"\r\n--test--\r\n"

        response = await self.client.post(
            "/api/runs", content=body(), headers={"Content-Type": "multipart/form-data; boundary=test"}
        )
        self.assertNotIn("Content-Length", response.request.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "upload_too_large")
        self.create_run.assert_not_awaited()


@unittest.skipUnless(TEST_DATABASE_URL, "Set TEST_DATABASE_URL to run the PostgreSQL acceptance checks.")
class RunPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema_name = f"phase2_test_{uuid4().hex}"
        schema = sql.Identifier(schema_name)
        admin = cls.enterClassContext(psycopg.connect(TEST_DATABASE_URL, autocommit=True))
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        cls.addClassCleanup(admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
        cls.database_url = make_conninfo(TEST_DATABASE_URL, options=f"-csearch_path={schema_name}")
        cls.database = cls.enterClassContext(psycopg.connect(cls.database_url, autocommit=True))
        for migration in sorted(MIGRATION.parent.glob("*.sql")):
            cls.database.execute(migration.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.client = self.enterContext(TestClient(app))
        self.pdf = small_pdf()
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": self.database_url,
            "MAX_UPLOAD_BYTES": str(len(self.pdf)),
        }))

    def upload(self) -> Response:
        return self.client.post("/api/runs", files={"file": ("test.pdf", self.pdf, "application/pdf")})

    def test_upload_persists_queued_run_and_protects_report(self) -> None:
        accepted = self.upload()
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.headers["cache-control"], "no-store")
        run_id = accepted.json()["run_id"]
        token = accepted.json()["access_token"]
        row = self.database.execute(
            "SELECT r.access_token_hash, u.pdf FROM runs r JOIN uploads u USING (run_id) WHERE run_id = %s",
            (UUID(run_id),),
        ).fetchone()
        self.assertEqual(row, (sha256(token.encode()).digest(), self.pdf))

        path = f"/api/runs/{run_id}"
        report = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.headers["cache-control"], "no-store")
        self.assertEqual({key: report.json()[key] for key in ("run_id", "state", "stage")},
                         {"run_id": run_id, "state": "queued", "stage": "queued"})
        self.assertEqual(report.json()["claims"], [])
        self.assertEqual(report.json()["progress"], {"total": 0, "checked": 0})
        other_run = self.upload()
        self.assertEqual(other_run.status_code, 202)
        other_token = other_run.json()["access_token"]
        self.assertNotEqual(other_token, token)
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {other_token}"}):
            with self.subTest(headers=headers):
                denied = self.client.get(path, headers=headers)
                self.assertEqual(denied.status_code, 404)
                self.assertEqual(denied.json()["error"]["code"], "report_unavailable")
                self.assertEqual(denied.headers["cache-control"], "no-store")

    def test_failed_upload_insert_rolls_back_run_and_upload(self) -> None:
        run_id = uuid4()
        self.database.execute(
            "ALTER TABLE uploads ADD CONSTRAINT reject_test_pdf CHECK (octet_length(pdf) = 0) NOT VALID"
        )
        self.addCleanup(self.database.execute, "ALTER TABLE uploads DROP CONSTRAINT reject_test_pdf")

        with patch("backend.app.runs.uuid4", return_value=run_id):
            with self.assertLogs("backend.app.runs", level="ERROR") as logs:
                response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "database_unavailable")
        self.assertIn("SQLSTATE 23514", logs.output[0])
        self.assertIsNone(self.database.execute("SELECT 1 FROM runs WHERE run_id = %s", (run_id,)).fetchone())
        self.assertIsNone(self.database.execute("SELECT 1 FROM uploads WHERE run_id = %s", (run_id,)).fetchone())
