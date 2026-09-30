"""Welcome to the Jungle's alert mail."""

import re

from email_adaptors.base import at


# "salary: £80-95k" and "salary above your minimum" — Welcome to the Jungle states pay on
# its own line, which is what marks the end of a card.
WTTJ_PAY = re.compile(r"^salary\b", re.I)


def welcometothejungle_alert(before, after, prefix) -> dict:
    # Company / what the company does / Title / "salary: ..." / "<location>  <url>".
    # The link ends the location line, so the card is the block above it and the location is
    # what precedes the link on the link's own line.
    pay_at = next((i for i in range(len(before) - 1, -1, -1) if WTTJ_PAY.match(before[i])),
                  len(before))
    # The body writes the link in parentheses, so the "(" is the last thing before it.
    place = re.sub(r"\s+", " ", str(prefix or "")).strip().rstrip("(").strip()
    return {"title": at(before, pay_at - 1),
            "company": at(before, pay_at - 3),
            "location": place}


TEMPLATES = [
    {"id": "welcometothejungle-alert", "provider": "welcometothejungle",
     "signature": re.compile(r"new jobs matching your search preferences", re.I),
     "adaptor": welcometothejungle_alert},
]
