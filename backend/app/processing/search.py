"""Tavily candidate discovery. Snippets are discovery hints, never evidence."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from backend.app.config import Settings
from backend.app.processing.claims import Claim


class SearchError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Candidate:
    url: str
    title: str


@dataclass(frozen=True)
class SearchResult:
    query: str
    candidates: tuple[Candidate, ...]


def identify_primary_domain(claim: Claim) -> str | None:
    """Conservative explicit publisher recognition, shared by both search passes."""
    publishers = {
        "nasa": "nasa.gov", "world bank": "worldbank.org",
        "world health organization": "who.int",
        "international monetary fund": "imf.org",
        "u.s. census bureau": "census.gov", "amazon": "ir.aboutamazon.com",
    }
    text = (claim.check_target + " " + claim.excerpt).casefold()
    return next((domain for publisher, domain in publishers.items()
                 if re.search(r"\b" + re.escape(publisher) + r"\b", text)), None)


def build_query(claim: Claim, *, follow_up: bool = False, primary_domain: str | None = None) -> str:
    """Drop disputed quantities on follow-up, retaining the claim's scope and period.

    The caller can identify a likely publisher; arbitrary publisher guesses are avoided.
    """
    target = claim.check_target
    if follow_up:
        target = re.sub(r"\b\d[\d,.]*(?:\s*(?:%|percent|million|billion|trillion))?", " ", target, flags=re.I)
    qualifiers = claim.qualifiers
    parts = [target, qualifiers.geography, qualifiers.population, qualifiers.units,
             qualifiers.reporting_period, qualifiers.other]
    if follow_up:
        if primary_domain is None:
            primary_domain = identify_primary_domain(claim)
        parts.append("official report data")
    if primary_domain:
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", primary_domain):
            raise ValueError("Use a publisher domain without a scheme or path.")
        parts.append(f"site:{primary_domain}")
    # Bound the query consistently for initial and follow-up searches.
    return " ".join(" ".join(part for part in parts if part).split()[:75])[:600].strip()


def search_sources(query: str, *, client: httpx.Client | None = None) -> SearchResult:
    settings = Settings.from_env()
    if not settings.tavily_api_key:
        raise SearchError("search_key_missing")
    if not query.strip() or len(query) > 600 or len(query.split()) > 75:
        raise ValueError("Search query must contain 1–600 characters and at most 75 words.")
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as owned:
            return search_sources(query, client=owned)
    # Translate provider-neutral site hints into Tavily's actual domain filter.
    domain_pattern = r"(?<!\S)site:([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?:/\S*)?(?=\s|$)"
    domains = list(dict.fromkeys(match.casefold() for match in re.findall(domain_pattern, query)))
    provider_query = re.sub(domain_pattern, "", query).strip()
    if not provider_query:
        raise ValueError("Search query must contain a fact as well as a domain filter.")
    request_body = {"query": provider_query, "search_depth": "basic", "max_results": 10,
                    "include_answer": False, "include_raw_content": False}
    if domains:
        request_body["include_domains"] = domains
    try:
        response = client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json=request_body, timeout=15,
        )
        if response.status_code in (401, 403):
            raise SearchError("search_credentials_rejected")
        if response.status_code == 429:
            raise SearchError("search_rate_limited")
        if response.status_code in (432, 433):
            raise SearchError("search_quota_exceeded")
        if response.status_code != 200:
            raise SearchError("search_provider_error")
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError
        if not isinstance(payload.get("results"), list):
            raise ValueError
        candidates = []
        seen = set()
        for item in payload["results"]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not isinstance(item.get("title"), str):
                raise ValueError
            url = item["url"]
            parsed = urlsplit(url)
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and url not in seen:
                candidates.append(Candidate(url, item["title"]))
                seen.add(url)
        return SearchResult(query, tuple(candidates))
    except httpx.TimeoutException:
        raise SearchError("search_timeout") from None
    except httpx.HTTPError:
        raise SearchError("search_request_failed") from None
    except (ValueError, TypeError):
        raise SearchError("search_invalid_response") from None
