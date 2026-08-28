"""LinkedIn search-card discovery, from LinkedIn's own guest endpoint.

LinkedIn publishes no job-search API that we can reach — Talent Solutions is partner-gated —
and its logged-in search is hostile to automation. What it does answer, unauthenticated, is
`/jobs-guest/jobs/api/seeMoreJobPostings/search`: an API-shaped URL returning a bare HTML
fragment of result cards. That puts this board between the other two special cases:

* Like Adzuna, it needs no browser. A plain `urllib` GET is the whole fetch — no crawl4ai, no
  Chromium, no anti-detection.
* Like Haystack, the cards are parsed out of HTML with BeautifulSoup rather than markdown,
  because the response has never been through a markdown converter at all.

`scan` only, like Talent and Haystack: full job descriptions are no longer collected.

The endpoint's parameters, its paging rule and the card selectors below are adapted from
JobSpy (MIT, Copyright (c) 2023 Cullen Watson) — `jobspy/linkedin/__init__.py`. They are
owned here rather than imported: JobSpy carries a pandas-shaped model layer this project has
no use for, and the part worth having is the forty lines that encode what LinkedIn does.

Two of its constants do not survive the copy. The endpoint returns **ten** cards per page,
not the twenty-five JobSpy names, so paging advances by what actually arrived; and LinkedIn
refuses to page past `start=1000`, so a search ends there whatever remains.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from board_config import (build_board_urls, jittered, linkedin_search_url, load_config,
                          raw_capture_stem, run_stamp)
import salary as salary_parser
import run_record
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "linkedin"
RAW = OUT / "raw"
REPORTS = OUT / "reports"

TIMEOUT_SECONDS = 30
# LinkedIn stops paging here regardless of how many results the search claims.
MAX_START = 1000
# Blocked, not empty: 429 is the documented throttle and 999 is LinkedIn's own refusal code.
BLOCKED_STATUSES = {429, 999}

# Borrowed from JobSpy, minus the headers urllib sets for itself. The guest endpoint serves a
# fragment to anything that looks like a browser; it is the request rate that gets noticed.
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}


@dataclass
class LinkedInLead:
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


def dedupe(leads: list[LinkedInLead]) -> list[LinkedInLead]:
    seen: dict[str, LinkedInLead] = {}
    for lead in leads:
        key = lead.job_id or "|".join([lead.role_title.lower(), lead.company.lower(), lead.location.lower()])
        if key not in seen:
            seen[key] = lead
    return list(seen.values())


@dataclass
class Response:
    """What scan_health classifies. The fragment plays the part the crawled page plays."""
    success: bool
    markdown: str = ""
    html: str = ""
    status_code: int | None = None
    error_message: str = ""


def fetch(url: str) -> Response:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            body = reply.read().decode("utf-8", errors="replace")
            return Response(True, html=body, status_code=reply.status)
    except urllib.error.HTTPError as failure:
        return Response(False, status_code=failure.code, error_message=str(failure))
    except (urllib.error.URLError, OSError) as failure:
        return Response(False, error_message=str(failure))


def job_id_from_href(href: str) -> str:
    """The numeric posting id out of a guest card's link.

    A guest href is `/jobs/view/<slug>-at-<company>-<id>?trackingId=...`: the query is
    per-impression, so it is dropped before anything else looks at the link, and the id is the
    slug's last hyphenated part. Anything else is not a posting link.
    """
    tail = (href or "").split("?")[0].rstrip("/").rsplit("-", 1)[-1]
    return tail if tail.isdigit() else ""


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_search_cards(html: str, spec: dict) -> list[LinkedInLead]:
    """Parse leads out of one page of the guest fragment.

    Card structure is JobSpy's: `div.base-search-card` per result, the accessible title in
    `span.sr-only` because the visible one is truncated with an ellipsis, and the posted date
    in `time[datetime]` because the visible text is relative ("2 weeks ago").
    """
    soup = BeautifulSoup(html or "", "html.parser")
    leads = []
    for card in soup.find_all("div", class_="base-search-card"):
        link = card.find("a", class_="base-card__full-link")
        job_id = job_id_from_href(link.get("href", "")) if link else ""
        if not job_id:
            continue
        company = card.find("h4", class_="base-search-card__subtitle")
        meta = card.find("div", class_="base-search-card__metadata")
        posted = meta.find("time") if meta else None
        lead = LinkedInLead(
            source="linkedin",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=_text(card.find("span", class_="sr-only")) or "Unknown role",
            company=_text(company.find("a") if company else None) or _text(company),
            # LinkedIn's UK guest cards almost never carry pay; when one does, it is prose.
            salary=_text(card.find("span", class_="job-search-card__salary-info")),
            location=_text(meta.find("span", class_="job-search-card__location") if meta else None),
            contract="",
            posted=posted.get("datetime", "") if posted else "",
            # Rebuilt rather than kept: the href carries per-impression tracking, so the same
            # advert would otherwise arrive under a different URL on every scan and never dedupe.
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            job_id=job_id,
            raw_block=card.get_text(" ", strip=True)[:600],
        )
        salary_parser.apply_to(lead)
        leads.append(lead)
    return leads


def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = (cfg.get("boards") or {}).get("linkedin") or {}
    if not board.get("enabled") and not allow_disabled:
        raise SystemExit("LinkedIn is disabled in config.yml. Use --allow-disabled for manual smoke tests.")
    specs = build_board_urls({**cfg, "boards": {**cfg.get("boards", {}), "linkedin": {**board, "enabled": True}}},
                             "linkedin")
    if limit:
        specs = specs[:limit]
    pages_per_search = int(board.get("pages_per_search", 2))
    distance = int(board.get("distance", 30))
    max_age_days = int(board.get("max_age_days", 0))
    delay = float(board.get("delay_seconds", (cfg.get("crawl") or {}).get("delay_seconds", 15)))

    RAW.mkdir(parents=True, exist_ok=True)
    with scan_lock.hold("linkedin"):
        stamp = run_stamp()
        with run_record.record("linkedin", stamp) as findings:
            health = scan_health.RunHealth("linkedin")
            all_leads: list[LinkedInLead] = []
            for spec in specs:
                print(f"Querying LinkedIn {spec['title']!r} / {spec['location']!r}")
                start = 0
                for page in range(pages_per_search):
                    url = linkedin_search_url(spec["title"], spec["location"], distance, max_age_days, start)
                    response = fetch(url)
                    stem = raw_capture_stem(
                        f"{slug(spec['title'])}__{slug(spec['location'])}__start{start}", stamp)
                    (RAW / f"{stem}.html").write_text(response.html or "", encoding="utf-8")
                    outcome = health.record(response)
                    if response.status_code in BLOCKED_STATUSES:
                        # Retrying a throttle is the one response guaranteed to make it worse.
                        print(f"  blocked status={response.status_code} — abandoning this search")
                        break
                    if outcome != scan_health.OK:
                        print(f"  {outcome} status={response.status_code} error={response.error_message}")
                        break
                    leads = parse_search_cards(response.html, spec)
                    print(f"  {outcome} status={response.status_code} start={start} leads={len(leads)}")
                    all_leads.extend(leads)
                    # Paging advances by what arrived, not by an assumed page size.
                    start += len(leads)
                    if not leads or start >= MAX_START:
                        break
                    if page + 1 < pages_per_search:
                        time.sleep(jittered(delay))
                time.sleep(jittered(delay))

            deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
            REPORTS.mkdir(parents=True, exist_ok=True)
            raw_path = REPORTS / f"linkedin_raw_{stamp}.json"
            dedup_path = REPORTS / f"linkedin_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
            dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
            print(f"LinkedIn raw={len(all_leads)} deduped={len(deduped)}")
            findings.update(jobs=len(deduped), searches=len(specs))
            print(f"Deduped JSON: {dedup_path}")
            health.finish()
            return dedup_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--allow-disabled", action="store_true",
                    help="manual smoke test even when boards.linkedin.enabled=false")
    args = ap.parse_args()
    scan(load_config(ROOT / args.config), args.limit, args.allow_disabled)


if __name__ == "__main__":
    main()
