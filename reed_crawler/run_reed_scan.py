from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

import yaml
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls
import scan_health
import scan_run
import scan_search
from reed_utils import parse_jobs_from_markdown, write_report

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "reed"


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


async def crawl_search(crawler: AsyncWebCrawler, spec: dict,
                       run_config: CrawlerRunConfig) -> scan_search.Fetched:
    """One search, and the three files Reed keeps of it.

    Reed is the only board that captures the page's link graph as well as its markdown and
    HTML. It predates the card parsers and was how the markdown parser was checked against
    what the page actually linked to.
    """
    print(f"Crawling {spec['title']!r} / {spec['location']!r}: {spec['url']}")
    result = await crawler.arun(url=spec["url"], config=run_config)
    md = str(result.markdown or "")

    links = []
    for group, arr in (result.links or {}).items():
        for link in arr:
            href = link.get("href") or ""
            links.append({
                "group": group,
                "text": (link.get("text") or "").strip()[:180],
                "url": urljoin(spec["url"], href),
            })
    captures = {"md": md, "html": result.html or "",
                "links.json": json.dumps(links, indent=2)}

    outcome = scan_health.classify(result)
    if outcome != scan_health.OK:
        # An empty body is a broken fetch, not a search with no matches — see scan_health.
        print(f"  {outcome} status={result.status_code} markdown={len(md)} error={result.error_message}")
        return scan_search.Fetched(result, captures=captures)

    jobs = parse_jobs_from_markdown(md, spec)
    print(f"  {outcome} status={result.status_code} markdown={len(md)} jobs={len(jobs)}")
    return scan_search.Fetched(result, jobs, captures=captures)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, default=None, help="limit number of search pages for smoke tests")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="scan even when the board is disabled; for manual smoke tests")
    args = ap.parse_args()

    cfg = load_config(ROOT / args.config)
    crawl_cfg = cfg.get("crawl", {})
    specs = build_board_urls(
        {**cfg, "boards": {**cfg.get("boards", {}), "reed": {**(cfg.get("boards") or {}).get("reed", {}), "enabled": True}}},
        "reed")
    if args.limit:
        specs = specs[: args.limit]

    browser_config = BrowserConfig(
        headless=bool(crawl_cfg.get("headless", True)),
        browser_type="chromium",
        verbose=True,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        remove_overlay_elements=True,
        remove_consent_popups=True,
        wait_for="css:body",
        page_timeout=int(crawl_cfg.get("page_timeout_ms", 45000)),
        delay_before_return_html=float(crawl_cfg.get("delay_before_return_html_seconds", 8)),
        scan_full_page=bool(crawl_cfg.get("scan_full_page", True)),
        scroll_delay=float(crawl_cfg.get("scroll_delay", 0.5)),
        screenshot=False,
    )

    async def fetches(spec):
        """One crawl per search."""
        yield await crawl_search(crawler, spec, run_config)

    with scan_run.begin("reed", cfg, label="Reed", allow_disabled=args.allow_disabled) as run:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            await scan_search.search(run, specs, fetches,
                                     delay=float(crawl_cfg.get("delay_seconds",
                                                               cfg.get("delay_seconds", 2))))

    # Reed alone also writes a readable summary beside its reports.
    report_md = run.report.with_name(f"reed_report_{run.stamp}.md")
    write_report(run.deduped, report_md)
    print(f"Report: {report_md}")


if __name__ == "__main__":
    asyncio.run(main())
