"""Bounded public-source fetching and deterministic passage selection."""

import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from backend.app.processing.claims import Claim
from backend.app.processing.pdf_text import PdfExtractionError, extract_pdf_text
from backend.app.processing.search import Candidate

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FETCH_SECONDS = 15
MAX_REDIRECTS = 3
logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceDocument:
    url: str
    body: bytes
    content_type: str
    retrieved_at: str


@dataclass(frozen=True)
class Evidence:
    source_url: str
    title: str
    retrieved_at: str
    publication_date: str | None
    reporting_period: str | None
    passage: str
    page_number: int | None


def _public_addresses(host: str, port: int, timeout: float) -> list[str]:
    # A separate process bounds DNS time; connections use only these exact IPs.
    script = "import socket,json,sys; print(json.dumps(list({r[4][0] for r in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)})))"
    try:
        result = subprocess.run([sys.executable, "-I", "-c", script, host, str(port)],
                                capture_output=True, timeout=timeout, check=True, env={})
        addresses = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RetrievalError("source_timeout") from None
    except (subprocess.CalledProcessError, OSError, ValueError):
        raise RetrievalError("source_unavailable") from None
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise RetrievalError("source_private_address")
    return addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((self.pinned_address, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def _request_validated(connection_class, host, port, addresses, path, deadline):
    # Some hosts publish IPv6 records even when the deployment has only IPv4
    # egress. Try only the already-validated answers, with one shared deadline.
    last_error = None
    for address in sorted(addresses, key=lambda value: ipaddress.ip_address(value).version):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RetrievalError("source_timeout")
        connection = connection_class(host, port, timeout=min(3, remaining))
        connection.pinned_address = address
        try:
            connection.request("GET", path, headers={"Accept-Encoding": "identity", "User-Agent": "FactCheckSourceReader/1.0"})
            if connection.sock:
                connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            return connection
        except (OSError, http.client.HTTPException) as error:
            logger.warning("Source transport failure type=%s errno=%s", type(error).__name__, getattr(error, 'errno', None))
            connection.close()
            last_error = error
    if last_error is not None:
        raise last_error
    raise RetrievalError("source_unavailable")


def fetch_source(url: str, *, max_bytes: int = MAX_RESPONSE_BYTES) -> SourceDocument:
    """No proxies, cookies or implicit redirects. TLS verifies the original hostname."""
    deadline = time.monotonic() + FETCH_SECONDS
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    for redirect in range(MAX_REDIRECTS + 1):
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise RetrievalError("source_invalid_url")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if port not in (80, 443):
                raise RetrievalError("source_invalid_url")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RetrievalError("source_timeout")
            addresses = _public_addresses(parsed.hostname, port, min(3, remaining))
            connection_class = _PinnedHTTPS if parsed.scheme == "https" else _PinnedHTTP
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection = _request_validated(connection_class, parsed.hostname, port, addresses, path, deadline)
            try:
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or redirect == MAX_REDIRECTS:
                        raise RetrievalError("source_redirect_limit")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    logger.warning("Source HTTP failure status=%s", response.status)
                    raise RetrievalError("source_unavailable")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise RetrievalError("source_unsupported_encoding")
                length = response.getheader("Content-Length")
                if length and int(length) > max_bytes:
                    raise RetrievalError("source_too_large")
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RetrievalError("source_timeout")
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(min(65536, max_bytes + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise RetrievalError("source_too_large")
                if time.monotonic() > deadline:
                    raise RetrievalError("source_timeout")
                final_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
                return SourceDocument(final_url, bytes(body), response.getheader("Content-Type", ""),
                                      datetime.now(timezone.utc).isoformat())
            finally:
                connection.close()
        except (socket.timeout, TimeoutError):
            raise RetrievalError("source_timeout") from None
        except (OSError, http.client.HTTPException):
            raise RetrievalError("source_unavailable") from None
        except ValueError:
            raise RetrievalError("source_invalid_response") from None
    raise RetrievalError("source_redirect_limit")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self.in_title = False
        self.hidden = 0
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag == "time" and attributes.get("datetime"):
            self.metadata.setdefault("date", attributes["datetime"])
        if tag == "meta":
            self.metadata[(attributes.get("property") or attributes.get("name") or "").lower()] = attributes.get("content", "")
        if tag in ("p", "div", "tr", "h1", "h2", "li", "br"):
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag in ("p", "div", "tr", "h1", "h2", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        elif not self.hidden:
            self.parts.append(data)


_STOP_WORDS = set("the a an is was were in on at of to and for with by from as has had percent million billion".split())


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{3,}", text.lower()) if word not in _STOP_WORDS}


def retrieve_evidence(candidate: Candidate, claim: Claim, *, fetcher=fetch_source) -> tuple[Evidence, ...]:
    document = fetcher(candidate.url)
    title = candidate.title
    publication_date = None
    period_metadata = None
    media_type = document.content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/pdf" or document.body.startswith(b"%PDF-"):
        try:
            pdf = extract_pdf_text(document.body, max_pages=100)
        except PdfExtractionError as error:
            raise RetrievalError(error.code) from None
        title = pdf.title or candidate.title
        sections = [(page.page_number, page.text) for page in pdf.pages]
    elif media_type in ("text/html", "application/xhtml+xml"):
        parser = _HTMLText()
        charset = re.search(r"charset=[\"']?([\w-]+)", document.content_type, re.I)
        try:
            parser.feed(document.body.decode(charset.group(1) if charset else "utf-8", errors="replace"))
        except (LookupError, ValueError):
            raise RetrievalError("source_invalid_html") from None
        title = " ".join(" ".join(parser.title_parts).split()) or parser.metadata.get("og:title") or candidate.title
        publication_date = parser.metadata.get("article:published_time") or parser.metadata.get("date") or None
        period_metadata = parser.metadata.get("reporting_period") or None
        sections = [(None, "".join(parser.parts))]
    else:
        raise RetrievalError("source_unsupported_type")
    query_text = claim.check_target + " " + claim.excerpt + " " + " ".join(
        value for value in claim.qualifiers.model_dump().values() if value)
    terms = _tokens(query_text)
    numbers = set(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", query_text))
    ranked = []
    for page_number, text in sections:
        # Bound passages while keeping adjacent table cells together.
        for block in re.split(r"\n+", text):
            block = " ".join(block.split())
            for start in range(0, len(block), 1200):
                passage = block[start:start + 1400]
                # Metric words alone can rank navigation above the actual figure.
                passage_numbers = set(re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", passage))
                word_score = len(terms & _tokens(passage))
                score = word_score + (3 * len(numbers & passage_numbers) if word_score else 0)
                if score:
                    # Only copy periods actually present in source text or metadata.
                    period = period_metadata
                    if not period:
                        match = re.search(
                            r"\b(?:in|during|for|FY|reporting period|fiscal year|year ended)\s+"
                            r"((?:19|20)\d{2}(?:\s*[-–/]\s*(?:(?:19|20)\d{2}|\d{2}))?)\b",
                            passage, re.I,
                        )
                        period = match.group(1) if match else None
                    ranked.append((score, Evidence(document.url, title, document.retrieved_at,
                                                   publication_date, period, passage, page_number)))
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    return tuple(entry[1] for entry in ranked[:5])
