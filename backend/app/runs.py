import logging
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.types import Message

from backend.app import run_store
from backend.app.config import Settings


router = APIRouter(prefix="/api/runs")
logger = logging.getLogger(__name__)
report_token = HTTPBearer(auto_error=False)
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadTooLarge(Exception):
    pass


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers=NO_STORE_HEADERS,
    )


def database_unavailable() -> JSONResponse:
    return error_response(503, "database_unavailable", "Runs are temporarily unavailable.")


def report_unavailable() -> JSONResponse:
    return error_response(404, "report_unavailable", "This report is unavailable.")


async def read_pdf(request: Request, max_upload_bytes: int) -> bytes | None:
    # Bound the raw multipart stream, then enforce the exact limit on the PDF part.
    remaining = max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    async def receive() -> Message:
        nonlocal remaining
        message = await request.receive()
        if message["type"] == "http.request":
            remaining -= len(message.get("body", b""))
            if remaining < 0:
                raise UploadTooLarge
        return message

    try:
        async with Request(request.scope, receive).form(max_files=1, max_fields=0) as form:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return None
            if upload.size is not None and upload.size > max_upload_bytes:
                raise UploadTooLarge
            pdf = await upload.read()
    except (HTTPException, ClientDisconnect):
        return None

    if len(pdf) > max_upload_bytes:
        raise UploadTooLarge
    return pdf if pdf.startswith(b"%PDF-") else None


@router.post(
    "",
    status_code=202,
    response_model=dict[str, str],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    },
                },
            },
        },
    },
)
async def submit_run(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(Settings.from_env)],
) -> dict[str, str] | JSONResponse:
    try:
        pdf = await read_pdf(request, settings.max_upload_bytes)
    except UploadTooLarge:
        return error_response(
            413, "upload_too_large", f"Upload a PDF no larger than {settings.max_upload_bytes} bytes."
        )
    if pdf is None:
        return error_response(400, "invalid_pdf", "Upload one valid PDF in the file field.")
    if settings.database_url is None:
        return database_unavailable()

    run_id = uuid4()
    access_token = token_urlsafe(32)
    try:
        await run_store.create_run(settings.database_url, run_id, sha256(access_token.encode()).digest(), pdf)
    except psycopg.Error as exc:
        logger.error("Could not store run %s (SQLSTATE %s)", run_id, exc.sqlstate)
        return database_unavailable()

    response.headers.update(NO_STORE_HEADERS)
    return {"run_id": str(run_id), "access_token": access_token}


@router.get("/{run_id}", response_model=None)
async def read_report(
    run_id: str,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(report_token)],
    settings: Annotated[Settings, Depends(Settings.from_env)],
) -> dict | JSONResponse:
    if credentials is None:
        return report_unavailable()
    try:
        run_uuid = UUID(run_id)
    except ValueError:
        return report_unavailable()
    if settings.database_url is None:
        return database_unavailable()

    try:
        run = await run_store.get_run(settings.database_url, run_uuid)
    except psycopg.Error as exc:
        logger.error("Could not read run %s (SQLSTATE %s)", run_uuid, exc.sqlstate)
        return database_unavailable()
    if run is None:
        return report_unavailable()
    state, stage, token_hash = run
    if not compare_digest(token_hash, sha256(credentials.credentials.encode()).digest()):
        return report_unavailable()

    response.headers.update(NO_STORE_HEADERS)
    try:
        details = await run_store.get_report_details(settings.database_url, run_uuid)
    except psycopg.Error as exc:
        logger.error("Could not read report %s (SQLSTATE %s)", run_uuid, exc.sqlstate)
        return database_unavailable()
    if details is None:
        return report_unavailable()
    return {"run_id": str(run_uuid), **details}
