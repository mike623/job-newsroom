"""24recruitmentmail.com: one sender, three layouts, one per board it re-mails.

The signatures are the mailer's own headings; the `a=<feed>` in every link agrees with
them but is not relied on, because a layout change is what breaks an adaptor and the
heading is what a layout change moves.
"""

import re

from email_adaptors.base import at


def joblookup_digest(before, after, prefix) -> dict:
    # Title / <url> / "NEW" / "Company, TOWN, COUNTY" / "more details" / <url> / "Apply" / <url>.
    # All three links are the same advert, so only the first attribution is kept.
    company, _, place = at(after, 0).partition(",")
    return {"title": at(before, -1), "company": company.strip(), "location": place.strip()}


def talent_digest(before, after, prefix) -> dict:
    # "Good Match" / Title / <url> / "Location, England, gb" / summary / "more details" / <url>.
    # These mails name no employer at all — the board's own scan is what states the company,
    # and inventing one here would poison dedup downstream.
    return {"title": at(before, -1), "company": "", "location": at(after, 0)}


def thebigjobsite_digest(before, after, prefix) -> dict:
    # Title / <url> / "Posted by" / Company / salary / contract / Location / "View job" / <url>.
    posted_at = next((i for i, line in enumerate(after) if re.fullmatch(r"posted by", line, re.I)), -1)
    if posted_at < 0:
        return {}
    return {"title": at(before, -1),
            "company": at(after, posted_at + 1),
            "location": at(after, -1)}


TEMPLATES = [
    {"id": "job24-joblookup", "provider": "job24",
     "signature": re.compile(r"you have some new matching jobs", re.I),
     "adaptor": joblookup_digest},
    {"id": "job24-talent", "provider": "job24",
     "signature": re.compile(r"Recommended Jobs for You", re.I), "adaptor": talent_digest},
    {"id": "job24-thebigjobsite", "provider": "job24",
     "signature": re.compile(r"Busy\? Not getting exactly matching jobs", re.I),
     "adaptor": thebigjobsite_digest},
]
