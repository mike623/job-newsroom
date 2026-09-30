"""Jobright's instant alert."""

import re

from email_adaptors.base import at


def jobright_alert(before, after, prefix) -> dict:
    # Company / "industry - stage" / NN% / "<title> (<url>)" / [salary] / Location.
    # Digest variants omit the inline title — it exists only in the HTML part.
    title = re.sub(r"\s*\|.*$", "", str(prefix or ""))
    title = re.sub(r"\bjob details\b", "", title, flags=re.I).strip()
    return {
        "title": title,
        "company": at(before, 0),
        "location": next((line for line in after if not re.match(r"^[£$€]", line)), ""),
    }


TEMPLATES = [
    {"id": "jobright-alert", "provider": "jobright",
     "signature": re.compile(r"Jobright Instant Alert|curated to align with your preferences", re.I),
     "adaptor": jobright_alert},
]
