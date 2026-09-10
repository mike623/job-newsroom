"""What differs between the aggregator boards, and nothing else.

An aggregator is a board that publishes many companies' postings from one endpoint. Unlike
the crawled boards, the *fetching* is identical across all of them — a plain GET returning
JSON or XML — so `aggregator_pipeline.py` holds one copy of it and this module holds the
table of what actually varies: the endpoint, how a page is addressed, where the records sit
in the reply, how one record becomes a lead, and how the board says it has run out of pages.

Pure data and pure functions. Nothing here does I/O or imports from the rest of the project,
so `board_config` can ask it what URLs a feed wants without a cycle.

Three kinds of narrowing, and the distinction matters more than it looks:

* `query_kind=None` — the endpoint takes no search parameter that works. A feed goes here
  unless narrowing was *observed* to change the result: Himalayas accepts `?search=` and
  returns the same rows for "kubernetes" and "nurse", and Remotive accepts `?search=` and
  `?limit=` and honours neither. Recording those as searchable would leave the board reading
  as filtered while it returned everything.
* `query_kind="titles"` — the parameter is free text, so it takes `title_groups` /
  `location_groups` from config, exactly as reed and adzuna do.
* `query_kind="vocab"` — the parameter is the API's own enum or slug, so a job title is the
  wrong sort of value and matches nothing. It reads a `queries:` list from the board block.

`query_required` is for boards too large to take whole: The Muse is 411,049 postings across
20,553 pages, so a scan without a query is a mistake, not a long afternoon.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

# Every page of every feed is one request; this is how a feed says there is not another one.
NO_MORE_PAGES: Callable[[object, int, list], bool] = lambda payload, page, rows: False

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def strip_html(text: str, limit: int = 600) -> str:
    """A description reduced to plain text for the lead's raw_block.

    Only ever an excerpt for a human reading the report, so a regex is the right tool —
    this is not parsing, and a parser here would pull a dependency in for nothing.
    """
    plain = _SPACE.sub(" ", unescape(_TAG.sub(" ", text or ""))).strip()
    return plain[:limit]


def without_query(url: str) -> str:
    """The posting's URL with any query string dropped.

    A feed that stamps its own attribution on every link hands back a different URL each time
    that value changes, and dedup never fires. See the invariant in CLAUDE.md.
    """
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) if parts.scheme else (url or "")


def _uk_date(text: str) -> str:
    """DevITjobs stamps a posting "04.10.2023"; downstream reads dates as ISO or not at all."""
    parts = (text or "").strip()[:10].split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        day, month, year = parts
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return (text or "").strip()[:10]


def text_of(element, tag: str) -> str:
    """An XML child's text, or "" — ElementTree returns None both for absent and for empty."""
    found = element.find(tag)
    return (found.text or "").strip() if found is not None else ""


@dataclass(frozen=True)
class Feed:
    name: str                     # the board name: outputs/<name>/ and the COMMANDS key
    label: str                    # "RemoteOK" — log lines and the search_title of a query-less feed
    host: str                     # the one host every request of this feed must land on
    page_url: Callable[[str, str, int, dict], str]   # (query, location, page, board_cfg) -> url
    rows: Callable[[object], list]                   # payload -> its list of records
    lead: Callable[[object, str], dict | None]       # record, label -> lead fields, or None to drop
    body: str = "json"            # "json" | "xml"
    capture_ext: str = "json"
    gzip: bool = False            # ask for, and decompress, a gzipped body
    query_kind: str | None = None
    query_required: bool = False
    more: Callable[[object, int, list], bool] = field(default=NO_MORE_PAGES)


# --------------------------------------------------------------------------- devitjobs

def _devitjobs_url(query: str, location: str, page: int, board_cfg: dict) -> str:
    return "https://devitjobs.uk/job_feed.xml"


def _devitjobs_rows(payload) -> list:
    return list(payload.findall("job"))


def _devitjobs_lead(row, label: str) -> dict | None:
    url = text_of(row, "url") or text_of(row, "link")
    if not url:
        return None
    city = text_of(row, "city")
    region = text_of(row, "region")
    return {
        "job_id": text_of(row, "id") or row.get("id", ""),
        "role_title": text_of(row, "title"),
        "company": text_of(row, "company") or text_of(row, "company-name"),
        # The only feed of the set quoting pay as advertiser prose ("£35,000 - 40,000 per
        # year"), so it is the only one that goes back through salary.parse_salary.
        "salary": text_of(row, "salary"),
        # City and region are often the same word ("London, London"); say it once.
        "location": ", ".join(dict.fromkeys(p for p in (city, region) if p)) or text_of(row, "location"),
        "contract": text_of(row, "jobtype") or text_of(row, "job-type"),
        "posted": _uk_date(text_of(row, "pubdate")),
        "url": without_query(url),
        "raw_block": strip_html(text_of(row, "description")),
    }


# ---------------------------------------------------------------------------- remoteok

def _remoteok_url(query: str, location: str, page: int, board_cfg: dict) -> str:
    return "https://remoteok.com/api"


def _remoteok_rows(payload) -> list:
    return payload if isinstance(payload, list) else []


def _remoteok_lead(row, label: str) -> dict | None:
    # Element 0 of the array is RemoteOK's legal notice, not a posting. It is recognised by
    # what it lacks rather than by its index, because an index is not a promise.
    if not isinstance(row, dict) or not row.get("position"):
        return None
    low, high = row.get("salary_min"), row.get("salary_max")
    salary = ""
    if low or high:
        low, high = int(low or high), int(high or low)
        salary = f"${low:,} per year" if low == high else f"${low:,} - ${high:,} per year"
    return {
        "job_id": str(row.get("id") or row.get("slug") or ""),
        "role_title": row.get("position") or "",
        "company": row.get("company") or "",
        "salary": salary,
        "location": row.get("location") or "",
        "contract": ", ".join(row.get("tags") or [])[:80],
        "posted": (row.get("date") or "")[:10],
        "url": without_query(row.get("url") or row.get("apply_url") or ""),
        "raw_block": strip_html(row.get("description") or ""),
        # The API gives numbers, so writing them back out of our own prose would only lose
        # precision — the same reasoning as adzuna_pipeline.salary_text.
        "salary_min": int(low) if low else None,
        "salary_max": int(high) if high else None,
        "salary_period": "year" if (low or high) else "",
    }


# ----------------------------------------------------------------------------- themuse

def _themuse_url(query: str, location: str, page: int, board_cfg: dict) -> str:
    params = {"page": page}
    if query:
        params["category"] = query
    if location:
        params["location"] = location
    return f"https://www.themuse.com/api/public/jobs?{urlencode(params)}"


def _themuse_rows(payload) -> list:
    return payload.get("results") or []


def _themuse_more(payload, page: int, rows: list) -> bool:
    return bool(rows) and page < int(payload.get("page_count") or 0)


def _themuse_lead(row, label: str) -> dict | None:
    url = ((row.get("refs") or {}).get("landing_page") or "")
    if not url:
        return None
    return {
        "job_id": str(row.get("id") or ""),
        "role_title": row.get("name") or "",
        "company": (row.get("company") or {}).get("name") or "",
        "salary": "",                       # The Muse states no pay on the public API.
        "location": ", ".join(p.get("name", "") for p in (row.get("locations") or []))[:120],
        "contract": ", ".join(p.get("name", "") for p in (row.get("levels") or [])),
        "posted": (row.get("publication_date") or "")[:10],
        "url": without_query(url),
        "raw_block": strip_html(row.get("contents") or ""),
    }


FEEDS: dict[str, Feed] = {
    "devitjobs": Feed(
        name="devitjobs", label="DevITjobs UK", host="devitjobs.uk",
        page_url=_devitjobs_url, rows=_devitjobs_rows, lead=_devitjobs_lead,
        # 11.25 MB of XML uncompressed against 2.46 MB gzipped, measured. urllib does not
        # negotiate this for us.
        body="xml", capture_ext="xml", gzip=True,
    ),
    "remoteok": Feed(
        name="remoteok", label="RemoteOK", host="remoteok.com",
        page_url=_remoteok_url, rows=_remoteok_rows, lead=_remoteok_lead,
    ),
    "themuse": Feed(
        name="themuse", label="The Muse", host="www.themuse.com",
        page_url=_themuse_url, rows=_themuse_rows, lead=_themuse_lead, more=_themuse_more,
        # &category= takes The Muse's own category names ("Software Engineering"), not job
        # titles — a title matches nothing at all.
        query_kind="vocab", query_required=True,
    ),
}


def queries_for(board: str, board_cfg: dict, titles: list[str]) -> list[str]:
    """What this feed should ask for, given its kind of narrowing."""
    feed = FEEDS[board]
    if feed.query_kind == "titles":
        chosen = titles
    elif feed.query_kind == "vocab":
        chosen = [str(q) for q in (board_cfg.get("queries") or []) if str(q).strip()]
    else:
        return [""]
    if not chosen and feed.query_required:
        key = "title_groups" if feed.query_kind == "titles" else "queries"
        raise SystemExit(
            f"{board} will not scan the whole board: set boards.{board}.{key} in config.yml."
        )
    return chosen or [""]


def build_urls(board: str, board_cfg: dict, titles: list[str], locations: list[str]) -> list[dict]:
    """One row per query — the same shape every other board's specs use.

    A feed that takes no query parameter yields exactly one row, whose title is the feed's
    label so the raw capture and the lead still say where they came from. Pages within a
    query are the pipeline's business, not this function's.
    """
    feed = FEEDS[board]
    places = locations if (feed.query_kind and locations) else [""]
    rows = []
    for query in queries_for(board, board_cfg, titles):
        for location in places:
            rows.append({
                "board": board,
                "title": query or feed.label,
                "location": location,
                "url": feed.page_url(query, location, 1, board_cfg),
            })
    return rows
