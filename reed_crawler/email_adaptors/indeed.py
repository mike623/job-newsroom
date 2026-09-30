"""Indeed's alert, match and single-role mails."""

import re

from email_adaptors.base import at


# "Salary: £49,000 - £59,000 a year" / "Job type: Full-time" — the first stated term is what
# marks the end of a card's identity in Indeed's single-role mail.
INDEED_TERMS = re.compile(r"^(salary|job type|work setting|pay|shift and schedule):", re.I)


def indeed_listing(before, after, prefix) -> dict:
    # Title / "Company - Location" / salary / ... / <url>
    company, _, location = at(before, 1).partition(" - ")
    return {"title": at(before, 0), "company": company, "location": location}


def indeed_role_listing(before, after, prefix) -> dict:
    # ...intro sentence / Title / Company / Location / "Salary: ..." / "Job type: ..." / link
    marked = next((i for i, line in enumerate(before) if INDEED_TERMS.match(line)), len(before))
    card = before[max(0, marked - 3):marked]
    return {"title": at(card, 0), "company": at(card, 1), "location": at(card, 2)}


TEMPLATES = [
    # One role, addressed personally: "...could align with this Senior Software Engineer role
    # at Edun Ltd". Listed before the generic match mail, whose wording it otherwise matches.
    {"id": "indeed-role-match", "provider": "indeed",
     "signature": re.compile(r"could (?:align with|be an? [a-z ]*match for) this .{0,80}? role at", re.I),
     "adaptor": indeed_role_listing},
    {"id": "indeed-job-alert", "provider": "indeed",
     "signature": re.compile(r"Indeed Job Alert", re.I), "adaptor": indeed_listing},
    # donotreply@match.indeed.com — different mail, same listing layout as the alert.
    {"id": "indeed-match", "provider": "indeed",
     "signature": re.compile(r"could be a match for this"
                             r"|based on your preferences, profile and activity on Indeed", re.I),
     "adaptor": indeed_listing},
]
