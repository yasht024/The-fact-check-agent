"""Extract page text in a time- and memory-limited subprocess."""

import json
import subprocess
import sys
from dataclasses import dataclass
from errno import ENOMEM
from io import BytesIO
from pathlib import Path


PARSE_TIMEOUT_SECONDS = 10
PARSER_MEMORY_BYTES = 256 * 1024 * 1024
PARSER_LIMIT_MESSAGE = "The PDF exceeds the parser's processing limits."


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class PdfText:
    pages: tuple[PageText, ...]
    skipped_pages: tuple[int, ...]
    title: str | None = None


class PdfExtractionError(Exception):
    def __init__(self, code: str, message: str, skipped_pages: tuple[int, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.skipped_pages = skipped_pages


def extract_pdf_text(pdf: bytes, *, max_pages: int) -> PdfText:
    """Return selectable text and skipped page numbers for an uploaded PDF."""
    if max_pages < 1:
        raise ValueError("max_pages must be positive.")

    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), str(max_pages), str(PARSER_MEMORY_BYTES)],
            input=pdf,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PARSE_TIMEOUT_SECONDS,
            env={},
        )
    except subprocess.TimeoutExpired:
        raise PdfExtractionError("pdf_parse_timeout", "The PDF took too long to read.") from None
    except OSError:
        raise PdfExtractionError("pdf_parser_unavailable", "The PDF parser could not start.") from None

    if completed.returncode != 0:
        raise PdfExtractionError("pdf_parse_failed", "The PDF parser stopped before it finished.")

    response = json.loads(completed.stdout)
    skipped_pages = tuple(response["skipped_pages"])
    if "error" in response:
        raise PdfExtractionError(response["error"], response["message"], skipped_pages)
    return PdfText(tuple(PageText(*page) for page in response["pages"]), skipped_pages, response.get("title"))


def _current_macos_address_space() -> int:
    import ctypes

    libsystem = ctypes.CDLL(None)
    task = ctypes.c_uint.in_dll(libsystem, "mach_task_self_")
    mach_task_basic_info = 20
    # mach_task_basic_info starts with the 64-bit virtual_size in bytes.
    info = (ctypes.c_uint64 * 16)()
    count = ctypes.c_uint(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int))
    if libsystem.task_info(task, mach_task_basic_info, ctypes.byref(info), ctypes.byref(count)) != 0:
        raise OSError("Could not read the parser's address space size.")
    return info[0]


def _limit_memory(memory_bytes: int) -> None:
    import resource

    if sys.platform == "darwin":
        # macOS maps a large shared region before Python starts. Limit growth above that baseline.
        memory_bytes += _current_macos_address_space()
    current_limit, _ = resource.getrlimit(resource.RLIMIT_AS)
    if current_limit != resource.RLIM_INFINITY:
        memory_bytes = min(memory_bytes, current_limit)
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _read_pages(pdf: bytes, *, max_pages: int) -> PdfText:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError, LimitReachedError, PyPdfError

    invalid_pdf = "The file could not be read. Upload a valid PDF."
    protected_pdf = "Remove the password protection and upload the PDF again."
    read_errors = (
        PyPdfError,
        DependencyError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        AssertionError,
        OverflowError,
        RecursionError,
    )

    if not pdf.startswith(b"%PDF-"):
        raise PdfExtractionError("invalid_pdf", invalid_pdf)
    try:
        reader = PdfReader(BytesIO(pdf))
        if reader.is_encrypted:
            raise PdfExtractionError("pdf_password_protected", protected_pdf)
        pages = reader.pages
        page_count = len(pages)
    except DependencyError:
        # PdfReader may need an optional AES implementation just to open an encrypted PDF.
        raise PdfExtractionError("pdf_password_protected", protected_pdf) from None
    except LimitReachedError:
        raise PdfExtractionError("pdf_parser_limit", PARSER_LIMIT_MESSAGE) from None
    except read_errors:
        raise PdfExtractionError("invalid_pdf", invalid_pdf) from None

    if page_count == 0:
        raise PdfExtractionError("invalid_pdf", invalid_pdf)
    if page_count > max_pages:
        raise PdfExtractionError("pdf_page_limit_exceeded", f"The PDF exceeds the limit of {max_pages} pages.")

    readable_pages = []
    skipped_pages = []
    has_unreadable_page = False
    for page_number in range(1, page_count + 1):
        try:
            text = pages[page_number - 1].extract_text().strip()
        except LimitReachedError:
            raise PdfExtractionError("pdf_parser_limit", PARSER_LIMIT_MESSAGE) from None
        except read_errors:
            has_unreadable_page = True
            text = ""
        if text:
            readable_pages.append(PageText(page_number, text))
        else:
            skipped_pages.append(page_number)

    if not readable_pages:
        if has_unreadable_page:
            raise PdfExtractionError("invalid_pdf", invalid_pdf, tuple(skipped_pages))
        raise PdfExtractionError(
            "pdf_no_selectable_text",
            "The PDF has no selectable text. Upload a PDF with a text layer.",
            tuple(skipped_pages),
        )
    metadata = reader.metadata
    title = str(metadata.title).strip() if metadata and metadata.title else None
    return PdfText(tuple(readable_pages), tuple(skipped_pages), title)


def _parse_in_subprocess(max_pages: int, memory_bytes: int) -> str:
    try:
        _limit_memory(memory_bytes)
    except (ImportError, AttributeError, OSError, ValueError):
        return json.dumps(
            {
                "error": "pdf_parser_unavailable",
                "message": "The PDF parser could not apply its memory limit.",
                "skipped_pages": [],
            }
        )

    try:
        from pypdf import overwrite_configuration

        # Image decoding is unnecessary for text extraction and would start another process.
        overwrite_configuration(jbig2dec_binary=None)
        result = _read_pages(sys.stdin.buffer.read(), max_pages=max_pages)
        return json.dumps(
            {
                "pages": [(page.page_number, page.text) for page in result.pages],
                "skipped_pages": result.skipped_pages,
                "title": result.title,
            }
        )
    except PdfExtractionError as error:
        return json.dumps({"error": error.code, "message": str(error), "skipped_pages": error.skipped_pages})
    except OSError as error:
        if error.errno != ENOMEM:
            raise
    except MemoryError:
        pass
    # The memory error response does not need to be serialized under the cap.
    return '{"error":"pdf_memory_limit","message":"The PDF exceeded the parser memory limit.","skipped_pages":[]}'


if __name__ == "__main__":
    print(_parse_in_subprocess(int(sys.argv[1]), int(sys.argv[2])))
