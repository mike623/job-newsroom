"""Haystack (haystack.cv) search-card discovery.

Haystack is a client-rendered aggregator, which changes two things versus the other crawled
boards:

* Its cards carry no field boundaries in the rendered markdown — a card's title runs straight
  into its company name — so cards are parsed out of the HTML, anchored on the icon that
  labels each field, rather than out of the markdown.
* Its search backend intermittently answers "Something went wrong loading jobs" on an
  otherwise healthy page. A search that comes back empty for that reason is retried once
  before being believed, so a quiet run says something about the board rather than the crawl.

`scan` only, like Talent: full job descriptions are no longer collected.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls, load_config, jittered
from lead import Lead
import scan_health
import scan_run
import scan_search

ROOT = Path(__file__).resolve().parents[1]

# Rendered when Haystack's own search backend fails; the page is otherwise a normal, empty result list.
SEARCH_ERROR = "Something went wrong loading jobs"


JOB_HREF = re.compile(r"/jobs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def haystack_job_id(url: str) -> str:
    m = JOB_HREF.search(url or "")
    return m.group(1) if m else ""


def crawl_config(cfg: dict) -> CrawlerRunConfig:
    crawl = cfg.get("crawl", {}) or {}
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=int(crawl.get("page_timeout_ms", 60000)),
        delay_before_return_html=float(crawl.get("delay_before_return_html_seconds", 10)),
        scan_full_page=bool(crawl.get("scan_full_page", True)),
        scroll_delay=float(crawl.get("scroll_delay", 1.5)),
        screenshot=False,
    )


def browser_config(cfg: dict) -> BrowserConfig:
    crawl = cfg.get("crawl", {}) or {}
    return BrowserConfig(
        headless=bool(crawl.get("headless", True)),
        browser_type="chromium",
        verbose=True,
        viewport_width=1920,
        viewport_height=1080,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )


# Each card field is introduced by a lucide icon. The surrounding utility classes are generated
# and churn with every redesign; the icon names say what the value means, so they are the anchor.
CARD_ICON_FIELDS = {
    "building2": "company",
    "map-pin": "location",
    "banknote": "salary",
    "clock": "posted",
}


def _icon_value(card, icon: str) -> str:
    svg = card.find("svg", class_=f"lucide-{icon}")
    if not svg:
        return ""
    span = svg.find_next_sibling("span")
    return span.get_text(" ", strip=True) if span else ""


def parse_search_cards(html: str, spec: dict) -> list[Lead]:
    """Parse job cards out of the rendered search HTML.

    Markdown is unusable here: Haystack emits a whole card as one link whose text concatenates
    title, company, location, salary and posted date with nothing between them.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    leads: list[Lead] = []
    for anchor in soup.find_all("a", href=JOB_HREF):
        url = urljoin(spec["url"], anchor.get("href") or "")
        jid = haystack_job_id(url)
        if not jid:
            continue
        heading = anchor.find(["h2", "h3"])
        fields = {name: _icon_value(anchor, icon) for icon, name in CARD_ICON_FIELDS.items()}
        leads.append(Lead(
            source="haystack",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=heading.get_text(" ", strip=True) if heading else "Unknown role",
            company=fields["company"],
            salary=fields["salary"],
            location=fields["location"],
            # Job type and category render as unlabelled badges that cannot be told apart, so
            # nothing is claimed for the contract rather than guessing which badge it is.
            contract="",
            posted=fields["posted"],
            url=url,
            job_id=jid,
            raw_block=anchor.get_text(" ", strip=True),
        ))
    return leads


async def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = scan_run.enabled(cfg, "haystack", allow_disabled)
    specs = build_board_urls({**cfg, "boards": {**cfg.get("boards", {}), "haystack": {**board, "enabled": True}}},
                             "haystack")
    if limit:
        specs = specs[:limit]
    delay = float(board.get("delay_seconds", (cfg.get("crawl") or {}).get("delay_seconds", 15)))

    with scan_run.begin("haystack", cfg, label="Haystack", allow_disabled=allow_disabled) as run:
        async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:

            async def fetches(spec):
                """One crawl per search, or two.

                Haystack's search backend intermittently answers "Something went wrong loading
                jobs" on an otherwise healthy page. That is the board failing, not the crawl,
                so it is retried once — and only the answer that counts is yielded, which keeps
                the retry out of the run's search tally.
                """
                print(f"Crawling Haystack {spec['title']!r} / {spec['location']!r}: {spec['url']}")
                r = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                leads = parse_search_cards(r.html or "", spec)
                if not leads and SEARCH_ERROR in str(r.markdown or ""):
                    print(f"  search backend error, retrying once after {delay:.0f}s")
                    await asyncio.sleep(jittered(delay))
                    r = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                    leads = parse_search_cards(r.html or "", spec)
                print(f"  status={r.status_code} {scan_health.classify(r)} leads={len(leads)}")
                yield scan_search.Fetched(r, leads, captures={"md": str(r.markdown or ""),
                                                              "html": r.html or ""})

            await scan_search.search(run, specs, fetches, delay=delay)
    return run.report


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, help="limit search pages for smoke tests")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="scan even when the board is disabled; for manual smoke tests")
    args = ap.parse_args()
    cfg = load_config(ROOT / args.config)
    if args.command == "scan":
        await scan(cfg, args.limit, args.allow_disabled)


if __name__ == "__main__":
    asyncio.run(main())
