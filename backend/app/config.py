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
    openai_api_key: str | None = field(repr=False)
    max_upload_bytes: int
    max_pdf_pages: int
    report_retention_hours: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            brave_search_api_key=os.environ.get("BRAVE_SEARCH_API_KEY") or None,
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            max_upload_bytes=_positive_env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
            max_pdf_pages=_positive_env_int("MAX_PDF_PAGES", 30),
            report_retention_hours=_positive_env_int("REPORT_RETENTION_HOURS", 24),
        )
