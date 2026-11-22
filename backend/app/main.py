from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.app.runs import router as runs_router


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="Fact-Checking Web App")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Register API routes above the frontend mount, which handles remaining paths.
app.include_router(runs_router)

if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    async def frontend_without_build() -> PlainTextResponse:
        return PlainTextResponse(
            "Build the frontend with `npm --prefix frontend run build` and restart the web service.",
            status_code=503,
        )
