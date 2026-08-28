"""Probe LinkedIn's guest job-search endpoint before committing to a board module.

LinkedIn has no public job-search API — Talent Solutions is partner-gated — but
`/jobs-guest/jobs/api/seeMoreJobPostings/search` answers an unauthenticated GET with a bare
HTML fragment of result cards. No auth, no JavaScript, no browser: this probe uses urllib,
not crawl4ai, the same way adzuna_pipeline does.

The only question it answers: does this IP get cards, or a 429/999 block? If it blocks, the
LinkedIn board is not worth building. Card selectors and the paging rule are adapted from
JobSpy (MIT, Copyright (c) 2023 Cullen Watson) — jobspy/linkedin/__init__.py.
"""
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup

ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
KEYWORDS = "senior software engineer"
LOCATION = "London, England, United Kingdom"
DISTANCE = 30
PAGES = 2
OUT = Path("outputs/linkedin/probe")

# JobSpy's headers, minus the ones urllib sets itself.
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def fetch(start: int) -> tuple[int, str]:
    params = {
        "keywords": KEYWORDS,
        "location": LOCATION,
        "distance": DISTANCE,
        "pageNum": 0,
        "start": start,
    }
    request = urllib.request.Request(f"{ENDPOINT}?{urlencode(params)}", headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def parse_cards(html: str) -> list[dict]:
    """Adapted from JobSpy's LinkedIn._process_job (MIT)."""
    soup = BeautifulSoup(html, "html.parser")
    leads = []
    for card in soup.find_all("div", class_="base-search-card"):
        link = card.find("a", class_="base-card__full-link")
        href = link["href"].split("?")[0] if link and link.has_attr("href") else ""
        job_id = href.rsplit("-", 1)[-1] if href else ""
        title = card.find("span", class_="sr-only")
        company = card.find("h4", class_="base-search-card__subtitle")
        company_link = company.find("a") if company else None
        meta = card.find("div", class_="base-search-card__metadata")
        location = meta.find("span", class_="job-search-card__location") if meta else None
        posted = meta.find("time") if meta else None
        pay = card.find("span", class_="job-search-card__salary-info")
        leads.append({
            "job_id": job_id,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}" if job_id.isdigit() else href,
            "title": title.get_text(strip=True) if title else "",
            "company": company_link.get_text(strip=True) if company_link else "",
            "location": location.get_text(strip=True) if location else "",
            "posted": posted.get("datetime", "") if posted else "",
            "salary": pay.get_text(" ", strip=True) if pay else "",
        })
    return leads


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pages, leads = [], []
    start = 0
    for page in range(PAGES):
        status, html = fetch(start)
        (OUT / f"linkedin_start{start}.html").write_text(html, encoding="utf-8")
        found = parse_cards(html) if status == 200 else []
        blocked = status in (429, 999) or "captcha" in html.lower()
        pages.append({
            "start": start,
            "status": status,
            "html_len": len(html),
            "cards": len(found),
            "blocked": blocked,
            "body_head": html[:300] if (blocked or not found) else "",
        })
        leads += found
        if blocked or not found:
            break
        start += len(found)
        if page + 1 < PAGES:
            time.sleep(random.uniform(3, 7))

    (OUT / "linkedin.leads.json").write_text(json.dumps(leads, indent=2), encoding="utf-8")
    numeric = [l for l in leads if l["job_id"].isdigit()]
    print(json.dumps({
        "endpoint": ENDPOINT,
        "keywords": KEYWORDS,
        "location": LOCATION,
        "pages": pages,
        "leads_total": len(leads),
        "numeric_ids": len(numeric),
        "distinct_ids": len({l["job_id"] for l in leads}),
        "with_salary": sum(1 for l in leads if l["salary"]),
        "outdir": str(OUT.resolve()),
        "sample": leads[:3],
    }, indent=2))


main()
