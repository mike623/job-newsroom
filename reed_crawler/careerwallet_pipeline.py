"""The Career Wallet search-card discovery, from the site's own server-rendered search.

thecareerwallet.com renders its results server-side: a plain GET of `/jobs?search=&location=`
returns the whole page as HTML, no JavaScript and no browser. So this board is shaped like
LinkedIn's — `urllib` for the fetch, BeautifulSoup for the cards — and unlike LinkedIn it is
an ordinary page rather than an API-shaped fragment.

`scan` only, like Talent, Haystack and LinkedIn: full job descriptions are no longer collected.

**The alert mail is not a route in.** The board mails a daily digest, but every job link in it
is `/stats/jbe/<blob>/<blob>` where the first blob is Laravel's `iv/value/mac` envelope: the IV
is fresh per send, so the same advert arrives under a different URL every morning and could
never dedupe, and there is no key here to decrypt it. `robots.txt` disallows `/stats/jbe/`, so
resolving one is not an option either, and the mail's HTML carries no canonical link anywhere.
The search page is the only place a Career Wallet posting states a stable identity — the
numeric id at the end of `/job/<slug>-<id>` — which is why this is a crawl board and there is
no `careerwallet` entry among the email labels.

The site republishes one advert under several SEO slugs ("<role>", "<company> needs to fill the
position of <role> fast", "apply today for <role> on <alerts>"), each with its own id. Those are
distinct postings as far as the site is concerned, and `lead.dedupe` collapses them by title and
company the way it collapses any board's duplicates.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from board_config import build_board_urls, careerwallet_search_url, load_config
from lead import Lead
import salary as salary_parser
import scan_health
import scan_run
import scan_search

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "careerwallet"

TIMEOUT_SECONDS = 30

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
}

# /job/<slug>-<numeric id> — the "Read more" link on a card, and the only stable identity the
# site publishes. The apply button and the title link are both /stats/jbe/ trackers.
JOB_PATH = re.compile(r"/job/[\w-]*?(\d+)$")


@dataclass
class Response:
    """What scan_health classifies. The page plays the part the crawled page plays."""
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
    """The numeric posting id out of a card's canonical link."""
    found = JOB_PATH.search((href or "").split("?")[0].rstrip("/"))
    return found.group(1) if found else ""


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_search_cards(html: str, spec: dict) -> list[Lead]:
    """Parse leads out of one page of results.

    A card is `div.job-list`. The title link and the apply button both point at a tracker, so
    the advert's URL is taken from the "Read more" link instead — that is the one the site
    publishes in its sitemap and the only one that stays the same between scans. The location
    is the `li` labelled with the map-marker icon, as the company's `li` carries no icon of its
    own; the surrounding utility classes are a bought theme's and will churn, but these
    `job-list-*` names are the site's own.

    Pay is not a field here. It appears only inside the description snippet when an advert
    happens to quote it, so an empty `salary` on this board is the board, not a parse failure.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    leads = []
    for card in soup.select("div.job-list"):
        link = card.select_one('.job-list-option a[href*="/job/"]')
        job_id = job_id_from_href(link.get("href", "")) if link else ""
        if not job_id:
            continue
        title = card.select_one(".job-list-title a")
        marker = card.select_one(".job-list-option li i.fa-map-marker-alt")
        leads.append(Lead(
            source="careerwallet",
            search_title=spec["title"],
            search_location=spec["location"],
            # The visible text is the full title, but the `title` attribute is the same string
            # without the theme's whitespace, so it is preferred where present.
            role_title=(title.get("title") if title else "") or _text(title) or "Unknown role",
            company=_text(card.select_one('.job-list-option li a[href*="/company/"]')),
            salary="",
            location=_text(marker.parent) if marker else "",
            contract="",
            # Relative ("22 hours ago"): the site states no absolute date on a card.
            posted=_text(card.select_one(".job-list-favourite-time span")),
            url=f"https://thecareerwallet.com{link.get('href', '')}"
                if link.get("href", "").startswith("/") else link.get("href", ""),
            job_id=job_id,
            raw_block=card.get_text(" ", strip=True)[:600],
        ))
        salary_parser.apply_to(leads[-1])
    return leads


async def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = scan_run.enabled(cfg, "careerwallet", allow_disabled)
    specs = build_board_urls(
        {**cfg, "boards": {**cfg.get("boards", {}), "careerwallet": {**board, "enabled": True}}},
        "careerwallet")
    if limit:
        specs = specs[:limit]
    pages_per_search = int(board.get("pages_per_search", 3))
    radius = int(board.get("radius", 40))
    delay = float(board.get("delay_seconds", (cfg.get("crawl") or {}).get("delay_seconds", 15)))

    async def fetches(spec):
        """A search is several pages: the site answers ten or so cards at a time.

        There is no result count to page against — the header states only "Showing Jobs: N -" —
        so a search ends when a page comes back with no cards, which is what the site returns
        past the last page rather than a 404. How many pages it may cost is scan_search's to
        enforce, not this loop's, so that the budget is stated in one place.
        """
        print(f"Querying Career Wallet {spec['title']!r} / {spec['location']!r}")
        page = 1
        while True:
            url = careerwallet_search_url(spec["title"], spec["location"], radius, page)
            response = await asyncio.to_thread(fetch, url)
            capture = {"html": response.html or ""}
            outcome = scan_health.classify(response)
            if outcome != scan_health.OK:
                print(f"  {outcome} status={response.status_code} error={response.error_message}")
                yield scan_search.Fetched(response, captures=capture, suffix=f"page{page}")
                return
            leads = parse_search_cards(response.html, spec)
            print(f"  {outcome} status={response.status_code} page={page} leads={len(leads)}")
            yield scan_search.Fetched(response, leads, captures=capture, suffix=f"page{page}")
            if not leads:
                return
            page += 1

    with scan_run.begin("careerwallet", cfg, label="Career Wallet",
                        allow_disabled=allow_disabled) as run:
        await scan_search.search(run, specs, fetches, delay=delay, pages_per_spec=pages_per_search)
    return run.report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--allow-disabled", action="store_true",
                    help="manual smoke test even when boards.careerwallet.enabled=false")
    args = ap.parse_args()
    asyncio.run(scan(load_config(ROOT / args.config), args.limit, args.allow_disabled))


if __name__ == "__main__":
    main()
