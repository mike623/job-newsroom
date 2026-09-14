from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

import yaml
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import board_locations, board_titles, jittered, raw_capture_stem
import scan_health
import scan_run
from reed_utils import SearchSpec, parse_jobs_from_markdown, write_report

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "reed"
RAW = OUT / "raw"


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_specs(cfg: dict) -> list[SearchSpec]:
    # Prefer the board-oriented config shape. Keep flat keys as a fallback for older configs.
    if cfg.get("boards", {}).get("reed"):
        board_cfg = cfg["boards"]["reed"]
        titles = board_titles(cfg, "reed")
        locations = board_locations(cfg, "reed")
        proximity = int(board_cfg.get("proximity", cfg.get("proximity", 50)))
        return [SearchSpec(t, loc, proximity) for t in titles for loc in locations]
    return [SearchSpec(t, loc, int(cfg.get("proximity", 50))) for t in cfg["titles"] for loc in cfg["locations"]]


async def crawl_search(crawler: AsyncWebCrawler, spec: SearchSpec, run_config: CrawlerRunConfig, stamp: str,
                       health: scan_health.RunHealth) -> list:
    print(f"Crawling {spec.title!r} / {spec.location!r}: {spec.url}")
    result = await crawler.arun(url=spec.url, config=run_config)
    md = str(result.markdown or "")
    html = result.html or ""

    links = []
    for group, arr in (result.links or {}).items():
        for link in arr:
            href = link.get("href") or ""
            links.append({
                "group": group,
                "text": (link.get("text") or "").strip()[:180],
                "url": urljoin(spec.url, href),
            })

    RAW.mkdir(parents=True, exist_ok=True)
    stem = raw_capture_stem(spec.name, stamp)
    (RAW / f"{stem}.md").write_text(md, encoding="utf-8")
    (RAW / f"{stem}.html").write_text(html, encoding="utf-8")
    (RAW / f"{stem}.links.json").write_text(json.dumps(links, indent=2), encoding="utf-8")

    outcome = health.record(result)
    if outcome != scan_health.OK:
        # An empty body is a broken fetch, not a search with no matches — see scan_health.
        print(f"  {outcome} status={result.status_code} markdown={len(md)} error={result.error_message}")
        return []

    jobs = parse_jobs_from_markdown(md, spec)
    print(f"  {outcome} status={result.status_code} markdown={len(md)} jobs={len(jobs)}")
    return jobs


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, default=None, help="limit number of search pages for smoke tests")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="scan even when the board is disabled; for manual smoke tests")
    args = ap.parse_args()

    cfg = load_config(ROOT / args.config)
    crawl_cfg = cfg.get("crawl", {})
    specs = build_specs(cfg)
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

    with scan_run.begin("reed", cfg, label="Reed", allow_disabled=args.allow_disabled) as run:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            for spec in specs:
                run.leads.extend(await crawl_search(crawler, spec, run_config, run.stamp, run.health))
                await asyncio.sleep(jittered(float(crawl_cfg.get("delay_seconds", cfg.get("delay_seconds", 2)))))
        run.searches = len(specs)

    # Reed alone also writes a readable summary beside its reports.
    report_md = run.report.with_name(f"reed_report_{run.stamp}.md")
    write_report(run.deduped, report_md)
    print(f"Report: {report_md}")


if __name__ == "__main__":
    asyncio.run(main())
