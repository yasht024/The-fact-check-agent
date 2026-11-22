"""Explicit live contract check. Run only from the configured worker environment."""

import re

from backend.app.config import Settings
from backend.app.processing.claims import extract_claims
from backend.app.processing.pdf_text import PageText
from backend.tests.test_claims import FIXTURE


def main() -> None:
    if not Settings.from_env().gemini_api_key:
        raise SystemExit("Live check pending. Set GEMINI_API_KEY in the worker environment.")
    claims = extract_claims([FIXTURE])
    assert len(claims) == 2, "Expected two independent assertions."
    assert all(claim.page_number == 2 and claim.excerpt in FIXTURE.text for claim in claims)
    for subject, value in (("revenue", 12), ("employment", 8)):
        assert any(
            subject in claim.check_target.casefold() and re.search(rf"\b{value}\b", claim.check_target)
            for claim in claims
        ), f"Expected a check for {subject} at {value}%."
    assert all(claim.qualifiers.reporting_period and "2024" in claim.qualifiers.reporting_period for claim in claims)
    assert all(claim.qualifiers.geography and "India" in claim.qualifiers.geography for claim in claims)
    promotional = extract_claims([PageText(1, "Discover a brighter tomorrow with our amazing solutions!")])
    assert promotional == [], "Expected no claims from promotional copy."
    print("Live Gemini claim contract passed for the two-figure and promotional fixtures.")


if __name__ == "__main__":
    main()
