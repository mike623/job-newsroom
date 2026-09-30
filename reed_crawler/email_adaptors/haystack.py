"""Haystack's alert mail."""

import re

from email_adaptors.base import at


def haystack_alert(before, after, prefix) -> dict:
    # "🔥 8 hours agoTechnology" / Title / "🏢 Company"
    # / "📍 Location 🇬🇧  •  💰 pay" / "Apply Now →" / <url>.
    # The emoji are the labels — the words around them are free text and the layout of a card
    # is otherwise indistinguishable from the digest's own headings.
    company_at = next((i for i, line in enumerate(before) if line.startswith("🏢")), -1)
    if company_at < 0:
        return {}
    place = next((line for line in before[company_at + 1:] if line.startswith("📍")), "")
    place = re.split(r"\s*[•|]", place.lstrip("📍 "))[0]
    place = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", place).strip().strip(",").strip()
    return {"title": at(before, company_at - 1),
            "company": before[company_at].lstrip("🏢 ").strip(),
            "location": place}


TEMPLATES = [
    {"id": "haystack-alert", "provider": "haystack",
     "signature": re.compile(r"NEW JOBS MATCHING YOUR SEARCH", re.I), "adaptor": haystack_alert},
]
