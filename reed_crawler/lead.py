"""One posting as a board found it, and what every board does with a list of them.

Every board writes its report as a list of these, so this is the shape `dashboard/aggregate.py`
reads back and the shape `ingest_jobspy.py` filters. It was declared nine times — once per board,
field for field identical, including Reed's older name for it, `Job` — which made the report row
a contract with no single place that stated it. Adding a field meant nine edits, and the one that
got missed was a board quietly writing a different shape.

What genuinely varies per board is how a lead is *recognised* — Adzuna's ids come from its API,
Indeed's sponsored adverts have none and fall back to the card — so `job_id` is derived in the
board and the key `dedupe` groups on is an argument.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass
class Lead:
    source: str
    search_title: str
    search_location: str
    role_title: str
    company: str
    salary: str
    location: str
    contract: str
    posted: str
    url: str
    job_id: str
    raw_block: str
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def identity(lead: Lead) -> str:
    """What makes two rows the same advert, for a board whose links carry an id.

    Where the link is not an identity — Indeed's sponsored adverts arrive under a new URL every
    morning — the board has already put title and company into `job_id`. This fallback is for a
    board that recovered no id at all from a card.
    """
    return lead.job_id or "|".join([lead.role_title.lower(), lead.company.lower(),
                                    lead.location.lower()])


def dedupe(leads: list[Lead], key: Callable[[Lead], str] = identity) -> list[Lead]:
    """One row per advert, keeping the first sighting.

    The first wins because a search page's own ordering is the board's relevance ranking, and a
    later search that found the same advert says nothing new about it.
    """
    seen: dict[str, Lead] = {}
    for lead in leads:
        found = key(lead)
        if found not in seen:
            seen[found] = lead
    return list(seen.values())
