"""LinkedIn's job alert mail."""

import re

from email_adaptors.base import at


# The lines a LinkedIn alert opens with, above its first card. BLOCK_NOISE strips the ones we
# have seen; this is the backstop for the one we have not.
LINKEDIN_INTRO = re.compile(r"^(your job alert for|an? new jobs? match|new jobs? match"
                            r"|\d+ new jobs?)", re.I)


def linkedin_listing(before, after, prefix) -> dict:
    """Title / Company / Location / ... / "View job: <url>"."""
    title = at(before, 0)
    if LINKEDIN_INTRO.match(title):
        # An intro line BLOCK_NOISE does not know yet. Saying nothing leaves the subject line
        # to answer; promoting this to a title shunts company and location along by one and
        # the row reads as a real job with invented fields.
        return {}
    return {"title": title, "company": at(before, 1), "location": at(before, 2)}


TEMPLATES = [
    {"id": "linkedin-job-alert", "provider": "linkedin",
     "signature": re.compile(r"^\s*View job:", re.I | re.M), "adaptor": linkedin_listing},
]
