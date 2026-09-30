"""Totaljobs' search digest and single-job recommendation."""

import re

from email_adaptors.base import at


TOTALJOBS_TERMS = re.compile(
    r"^(permanent|contract|temporary|full[- ]?time|part[- ]?time|freelance|apprenticeship"
    r"|from [£$€]|up to [£$€]|[£$€]|competitive|(starting |basic )?salary\b)", re.I)


def totaljobs_digest(before, after, prefix) -> dict:
    # Title / <url> / Company / Location / contract / salary
    return {"title": at(before, -1), "company": at(after, 0), "location": at(after, 1)}


def totaljobs_recommendation(before, after, prefix) -> dict:
    # ...intro / Title / Company / Location / contract / salary / "Apply Now" / <url> / JD text
    tail = [line for line in before if not TOTALJOBS_TERMS.match(line)][-3:]
    return {"title": at(tail, 0), "company": at(tail, 1), "location": at(tail, 2)}


TEMPLATES = [
    {"id": "totaljobs-search-digest", "provider": "totaljobs",
     "signature": re.compile(r"new jobs that match your search|Check out your latest matches"
                             r"|Picked for you", re.I),
     "adaptor": totaljobs_digest},
    {"id": "totaljobs-recommendation", "provider": "totaljobs",
     "signature": re.compile(r"We recommend this job for you", re.I),
     "adaptor": totaljobs_recommendation},
]
