"""talent.com's own daily alert, read from its HTML part."""

import re

from email_adaptors.base import at


def talent_alert(before, after, prefix) -> dict:
    # Badge / Title / <url> / Location / Company / "Good match" / "Near you" / <url>. Both links
    # are the same advert, so only the first attribution is kept.
    return {"title": at(before, -1), "company": at(after, 1), "location": at(after, 0)}


TEMPLATES = [
    # The heading's "." is U+2024 ONE DOT LEADER, not a full stop.
    {"id": "talent-alert", "provider": "talent",
     "signature": re.compile(r"Your Talent\W?com daily job alert", re.I), "adaptor": talent_alert},
]
