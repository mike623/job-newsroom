from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls, load_config, jittered, raw_capture_stem, run_stamp
from lead import Lead, dedupe, slug
import salary as salary_parser
import run_record
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "talent"
RAW = OUT / "raw"
REPORTS = OUT / "reports"


def talent_job_id(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("id", "jobid", "job_id", "source"):
        if qs.get(key):
            return qs[key][0]
    match = re.search(r"/view\?id=([^&]+)", url)
    if match:
        return match.group(1)
    # Stable fallback for search-card discovery dedupe when Talent uses slug URLs.
    path = parsed.path.strip("/")
    return slug(path or url, 96)


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


def looks_like_talent_job_url(url: str) -> bool:
    parsed = urlparse(url)
    if "talent.com" not in parsed.netloc:
        return False
    if parsed.path.startswith("/view") and parse_qs(parsed.query).get("id"):
        return True
    if "/job/" in parsed.path or "/jobs/" in parsed.path:
        return "?k=" not in url and "&l=" not in url
    return False


def parse_links(result, spec: dict) -> list[Lead]:
    leads = []
    for _, arr in (result.links or {}).items():
        for link in arr or []:
            href = link.get("href") or ""
            text = (link.get("text") or "").strip()
            url = urljoin(spec["url"], href)
            if not looks_like_talent_job_url(url):
                continue
            if not text or text.lower() in {"apply", "view job", "see more", "save", "next"}:
                continue
            leads.append(Lead(
                source="talent",
                search_title=spec["title"],
                search_location=spec["location"],
                role_title=text[:180],
                company="",
                salary="",
                location="",
                contract="",
                posted="",
                url=url,
                job_id=talent_job_id(url),
                raw_block=text,
            ))
    return leads


def parse_markdown_cards(markdown: str, spec: dict) -> list[Lead]:
    leads: list[Lead] = []
    lines = [ln.strip() for ln in markdown.splitlines()]
    for i, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        title = line[3:].strip()
        if not title or title.lower().startswith(("job type", "job description")):
            continue
        company = location = contract = posted = ""
        raw_parts: list[str] = [title]
        show_url = ""
        for nxt in lines[i + 1:i + 9]:
            if not nxt or nxt.startswith("!"):
                continue
            raw_parts.append(nxt)
            if "•" in nxt and not company:
                company, location = [part.strip() for part in nxt.split("•", 1)]
            elif not contract and re.search(r"\b(full-time|part-time|permanent|contract|temporary)\b", nxt, re.I):
                contract = nxt
            if not posted and re.search(r"last updated|new!|days ago|hours ago", nxt, re.I):
                posted = nxt
            match = re.search(r"\[Show more\]\((https://uk\.talent\.com/view\?id=[^)]+)\)", nxt)
            if match:
                show_url = match.group(1)
                break
        if not show_url:
            continue
        leads.append(Lead(
            source="talent",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=title,
            company=company,
            salary="",
            location=location,
            contract=contract,
            posted=posted,
            url=show_url,
            job_id=talent_job_id(show_url),
            raw_block="\n".join(raw_parts),
        ))
    return leads


def parse_result(result, spec: dict) -> list[Lead]:
    markdown_leads = parse_markdown_cards(str(result.markdown or ""), spec)
    if markdown_leads:
        return markdown_leads
    return parse_links(result, spec)


async def scan(cfg: dict, limit: int | None = None) -> Path:
    board = cfg.get("boards", {}).get("talent", {})
    if not board.get("enabled"):
        raise SystemExit("Talent.com is disabled in config.yml")
    specs = build_board_urls(cfg, "talent")
    if limit:
        specs = specs[:limit]
    RAW.mkdir(parents=True, exist_ok=True)
    with scan_lock.hold("talent"):
        stamp = run_stamp()
        with run_record.record("talent", stamp) as findings:
            health = scan_health.RunHealth("talent")
            all_leads: list[Lead] = []
            async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
                for spec in specs:
                    print(f"Crawling Talent.com {spec['title']!r} / {spec['location']!r}: {spec['url']}")
                    r = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                    md = str(r.markdown or "")
                    html = r.html or ""
                    stem = raw_capture_stem(f"{slug(spec['title'])}__{slug(spec['location'])}", stamp)
                    (RAW / f"{stem}.md").write_text(md, encoding="utf-8")
                    (RAW / f"{stem}.html").write_text(html, encoding="utf-8")
                    leads = parse_result(r, spec)
                    print(f"  status={r.status_code} {health.record(r)} leads={len(leads)}")
                    all_leads.extend(leads)
                    await asyncio.sleep(jittered(float(board.get("delay_seconds", (cfg.get("crawl") or {}).get("delay_seconds", 45)))))
            for lead in all_leads:
                salary_parser.apply_to(lead)
            deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
            REPORTS.mkdir(parents=True, exist_ok=True)
            raw_path = REPORTS / f"talent_raw_{stamp}.json"
            dedup_path = REPORTS / f"talent_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
            dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
            print(f"Talent raw={len(all_leads)} deduped={len(deduped)}")
            findings.update(jobs=len(deduped), searches=len(specs))
            print(f"Deduped JSON: {dedup_path}")
            health.finish()
            return dedup_path


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, help="limit search pages for smoke tests")
    args = ap.parse_args()
    cfg = load_config(ROOT / args.config)
    if args.command == "scan":
        await scan(cfg, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
