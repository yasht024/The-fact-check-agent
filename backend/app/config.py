import os
from dataclasses import dataclass, field


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer.") from None

    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(repr=False)
    brave_search_api_key: str | None = field(repr=False)
    tavily_api_key: str | None = field(repr=False)
    gemini_api_key: str | None = field(repr=False)
    gemini_model: str
    processing_deadline_seconds: int
    max_claims: int
    max_searches_per_claim: int
    max_upload_bytes: int
    max_pdf_pages: int
    report_retention_hours: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            brave_search_api_key=os.environ.get("BRAVE_SEARCH_API_KEY", "").strip() or None,
            tavily_api_key=os.environ.get("TAVILY_API_KEY", "").strip() or None,
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip() or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite",
            processing_deadline_seconds=_positive_env_int("PROCESSING_DEADLINE_SECONDS", 600),
            max_claims=_positive_env_int("MAX_CLAIMS", 50),
            max_searches_per_claim=_positive_env_int("MAX_SEARCHES_PER_CLAIM", 4),
            max_upload_bytes=_positive_env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
            max_pdf_pages=_positive_env_int("MAX_PDF_PAGES", 30),
            report_retention_hours=_positive_env_int("REPORT_RETENTION_HOURS", 24),
        )
