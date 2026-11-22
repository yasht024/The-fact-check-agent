"""Separate public-data check: worker discovery, then retrieve the NASA page."""
import asyncio
from urllib.parse import urlsplit

from backend.app.processing.claims import Claim, Qualifiers
from backend.app.processing.retrieval import RetrievalError, retrieve_evidence
from backend.app.processing.search import SearchError
from backend.app.processing.worker import discover_sources


def main():
    claim = Claim(excerpt="Earth's equatorial diameter is 12,756 kilometers.", page_number=1,
                  check_target="NASA Earth facts equatorial diameter 12,756 kilometers",
                  qualifiers=Qualifiers(units="kilometers", geography=None, population=None,
                                        reporting_period=None, other=None))
    try:
        result = asyncio.run(discover_sources(claim))
        known = [candidate for candidate in result.candidates
                 if urlsplit(candidate.url).hostname == "science.nasa.gov"
                 and urlsplit(candidate.url).path.rstrip("/") == "/earth/facts"]
        assert known, "Tavily did not return the known NASA Earth facts page."
        print(f"Live Tavily worker check passed: {known[0].url}", flush=True)
        evidence = retrieve_evidence(known[0], claim)
        assert evidence and any("diameter" in item.passage.lower() for item in evidence), "Expected the source's diameter passage."
        assert all(item.title and item.retrieved_at for item in evidence)
        print(f"Live source retrieval passed: {len(evidence)} passages; {evidence[0].source_url}")
    except (SearchError, RetrievalError) as error:
        raise SystemExit(f"Live source check failed: {error.code}") from None


if __name__ == "__main__":
    main()
