from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import yaml
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from board_config import build_board_urls, load_config, jittered, raw_capture_stem
from lead import Lead, slug
import scan_run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "totaljobs"
JOB_PAGES = OUT / "job_pages"
REPORTS = OUT / "reports"
DEFAULT_CAREER_OPS = Path(os.environ.get("CAREER_OPS_WORKSPACE") or ROOT.parent / "career-ops")


def totaljobs_job_id(url: str) -> str:
    m = re.search(r"job(\d+)", url)
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


# Badges Totaljobs renders after the posted date; they are not part of the card's data.
CARD_BADGES = {"featured", "premium", "new", "easy apply", "quick apply", "top employer", "blue chip"}

# Totaljobs prints this in the salary slot when the advert declines to state one.
SALARY_PLACEHOLDERS = {"unspecified", "not specified", "salary not specified"}

CARD_HEADING = re.compile(r"^## \[(?P<title>.+?)\]\((?P<url>\S+?)\)\s*$")


def _card_blocks(markdown: str) -> list[tuple[str, str, list[str]]]:
    """Split search-result markdown into (title, url, body lines) per job card.

    Logo lines are dropped — they belong to the *next* card, not the one they follow.
    """
    lines = [ln.rstrip() for ln in markdown.splitlines()]
    starts = [i for i, ln in enumerate(lines) if CARD_HEADING.match(ln)]
    blocks = []
    for n, i in enumerate(starts):
        m = CARD_HEADING.match(lines[i])
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = [ln.strip() for ln in lines[i + 1:end] if ln.strip() and not ln.startswith("[![")]
        blocks.append((m.group("title").strip(), m.group("url").strip(), body))
    return blocks


def parse_search_cards(markdown: str, spec: dict) -> list[Lead]:
    """Parse the search cards themselves, rather than harvesting the page's link graph.

    Every observed card renders exactly four lines before a literal "more", in the order
    company / location / salary / snippet, then the posted date. Anchoring on "more" means a
    layout change yields blank fields instead of silently shifting a snippet into the salary.
    """
    leads: list[Lead] = []
    for title, url, body in _card_blocks(markdown):
        url = urljoin(spec["url"], url)
        if "totaljobs.com/job/" not in url:
            continue
        jid = totaljobs_job_id(url)
        if not jid:
            continue

        company = location = salary = posted = ""
        if "more" in body:
            cut = body.index("more")
            head, tail = body[:cut], body[cut + 1:]
            if len(head) >= 1:
                company = head[0]
            if len(head) >= 2:
                location = head[1]
            # Only trust the salary slot when the card has its full complement of lines;
            # a short card would otherwise donate its snippet to the salary field.
            if len(head) >= 4 and head[2].lower() not in SALARY_PLACEHOLDERS:
                salary = head[2]
            posted = next((t for t in tail if t.lower() not in CARD_BADGES), "")

        leads.append(Lead(
            source="totaljobs",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=title or "Unknown role",
            company=company,
            salary=salary,
            location=location,
            contract="",
            posted=posted,
            url=url,
            job_id=jid,
            raw_block="\n".join([title, *body]),
        ))
    return leads


def parse_result(result, spec: dict) -> list[Lead]:
    """Prefer card parsing; fall back to the link graph if the markdown shape is unrecognised."""
    cards = parse_search_cards(str(result.markdown or ""), spec)
    if cards:
        return cards
    return parse_search_links(result, spec, str(result.markdown or ""))


def parse_search_links(result, spec: dict, markdown: str) -> list[Lead]:
    links = []
    for group, arr in (result.links or {}).items():
        for link in arr or []:
            href = link.get("href") or ""
            text = (link.get("text") or "").strip()
            url = urljoin(spec["url"], href)
            if "totaljobs.com/job/" not in url:
                continue
            jid = totaljobs_job_id(url)
            if not jid:
                continue
            links.append(Lead(
                source="totaljobs",
                search_title=spec["title"],
                search_location=spec["location"],
                role_title=text or "Unknown role",
                company="",
                salary="",
                location="",
                contract="",
                posted="",
                url=url,
                job_id=jid,
                raw_block=text,
            ))
    return links


async def scan_searches(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = scan_run.enabled(cfg, "totaljobs", allow_disabled)
    specs = build_board_urls({**cfg, "boards": {**cfg.get("boards", {}), "totaljobs": {**board, "enabled": True}}},
                             "totaljobs")
    if limit:
        specs = specs[:limit]

    with scan_run.begin("totaljobs", cfg, label="Totaljobs", allow_disabled=allow_disabled) as run:
        async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
            for spec in specs:
                print(f"Crawling Totaljobs {spec['title']!r} / {spec['location']!r}: {spec['url']}")
                result = await crawler.arun(url=spec["url"], config=crawl_config(cfg))
                md = str(result.markdown or "")
                html = result.html or ""
                stem = raw_capture_stem(f"{slug(spec['title'])}__{slug(spec['location'])}", run.stamp)
                (run.raw_dir / f"{stem}.md").write_text(md, encoding="utf-8")
                (run.raw_dir / f"{stem}.html").write_text(html, encoding="utf-8")
                leads = parse_result(result, spec)
                print(f"  status={result.status_code} {run.health.record(result)} leads={len(leads)}")
                run.leads.extend(leads)
                await asyncio.sleep(jittered(float((cfg.get("crawl") or {}).get("delay_seconds", 15))))
        run.searches = len(specs)
    return run.report


def latest_deduped() -> Path:
    files = sorted(REPORTS.glob("totaljobs_deduped_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No totaljobs_deduped_*.json found. Run scan first.")
    return files[0]


def extract_detail(md: str) -> dict:
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    title = company = location = contract = posted = salary = ""
    for i, ln in enumerate(lines):
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
            # Common Totaljobs header lines follow the title.
            tail = lines[i+1:i+8]
            for t in tail:
                if not company and not t.startswith("*") and "Apply" not in t and "Save" not in t:
                    company = re.sub(r"View Profile$", "", t).strip()
                if t.startswith("*"):
                    val = t.lstrip("* ").strip()
                    if "£" in val and not salary:
                        salary = val
                    elif any(x in val.lower() for x in ["permanent", "contract", "full-time", "part-time"]) and not contract:
                        contract = val
                    elif re.search(r"published|ago|today|yesterday", val, re.I) and not posted:
                        posted = val
                    elif not location:
                        location = val
            break
    # Remove the header/nav before the first title; keep rest as JD evidence.
    jd = md
    if title:
        idx = md.find(f"# {title}")
        if idx >= 0:
            jd = md[idx:].strip()
    stop = len(jd)
    for pat in [r"\nSimilar jobs", r"\nRecommended jobs", r"\nJobs by", r"\nApply\b"]:
        m = re.search(pat, jd, flags=re.I)
        if m and m.start() > 500:
            stop = min(stop, m.start())
    return {"title": title, "company": company, "location": location, "contract": contract, "posted": posted, "salary": salary, "full_jd": jd[:stop].strip()}


async def enrich(cfg: dict, input_path: Path | None = None, top: int | None = None, delay: float | None = None) -> Path:
    input_path = input_path or latest_deduped()
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    board = cfg.get("boards", {}).get("totaljobs", {})
    full = board.get("full_jd", {}) or {}
    top_n = top if top is not None else int(full.get("top_n", 5))
    delay_s = delay if delay is not None else float(full.get("delay_seconds", 20))
    jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:top_n]
    JOB_PAGES.mkdir(parents=True, exist_ok=True)
    enriched = []
    async with AsyncWebCrawler(config=browser_config(cfg)) as crawler:
        for job in jobs:
            print(f"Full JD Totaljobs {job.get('job_id')}: {job.get('role_title')}")
            result = await crawler.arun(url=job["url"], config=crawl_config(cfg))
            md = str(result.markdown or "")
            html = result.html or ""
            detail = extract_detail(md)
            job_id = job.get("job_id") or slug(job.get("url", ""), 32)
            (JOB_PAGES / f"{job_id}.md").write_text(md, encoding="utf-8")
            (JOB_PAGES / f"{job_id}.html").write_text(html, encoding="utf-8")
            merged = dict(job)
            for k in ["title", "company", "location", "contract", "posted", "salary"]:
                if detail.get(k):
                    merged[{"title": "role_title"}.get(k, k)] = detail[k]
            merged.update({
                "full_jd_crawled": bool(result.success),
                "full_jd_status_code": result.status_code,
                "full_jd_error": result.error_message,
                "full_jd_markdown_path": str(JOB_PAGES / f"{job_id}.md"),
                "full_jd_html_path": str(JOB_PAGES / f"{job_id}.html"),
                "full_jd": detail.get("full_jd", ""),
                "full_jd_length": len(detail.get("full_jd", "")),
                "evidence_level": "full_jd" if result.success and len(detail.get("full_jd", "")) > 500 else "search_result_card",
            })
            print(f"  status={result.status_code} success={result.success} full_jd_len={merged['full_jd_length']}")
            enriched.append(merged)
            await asyncio.sleep(delay_s)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = REPORTS / f"totaljobs_enriched_full_jd_{stamp}.json"
    out.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    print(f"Enriched JSON: {out}")
    return out


def latest_enriched() -> Path:
    files = sorted(REPORTS.glob("totaljobs_enriched_full_jd_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No totaljobs_enriched_full_jd_*.json found. Run enrich first.")
    return files[0]


def load_pipeline(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else "# Job Pipeline\n\n## Pendientes\n\n## Processed\n"


def insert_pending(pipeline: str, entries: list[str]) -> str:
    if not entries:
        return pipeline
    block = "\n".join(entries) + "\n"
    m = re.search(r"(^##\s+Pendientes\s*$)", pipeline, flags=re.M) or re.search(r"(^##\s+Pending\s*$)", pipeline, flags=re.M)
    if m:
        return pipeline[:m.end()] + "\n" + block + pipeline[m.end():]
    return pipeline.rstrip() + "\n\n## Pendientes\n" + block


def export_to_career_ops(cfg: dict, input_path: Path | None = None, dry_run: bool = False) -> None:
    input_path = input_path or latest_enriched()
    career_ops = Path((cfg.get("career_ops") or {}).get("workspace") or DEFAULT_CAREER_OPS)
    jds_dir = career_ops / "jds"
    pipeline_path = career_ops / "data" / "pipeline.md"
    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    pipeline = load_pipeline(pipeline_path)
    entries = []
    imported = skipped = 0
    for job in jobs:
        if job.get("evidence_level") != "full_jd" or not (job.get("full_jd") or "").strip():
            skipped += 1
            continue
        job_id = job.get("job_id") or slug(job.get("url", ""), 32)
        company = job.get("company") or "Unknown company"
        role = job.get("role_title") or "Unknown role"
        jd_name = f"totaljobs-{job_id}-{slug(company, 32)}-{slug(role, 44)}.md"
        jd_rel = f"jds/{jd_name}"
        if job.get("url", "") in pipeline or f"local:{jd_rel}" in pipeline or job_id in pipeline:
            skipped += 1
            continue
        body = f"""---
source: totaljobs
source_url: {json.dumps(job.get('url') or '')}
totaljobs_job_id: {json.dumps(job_id)}
company: {json.dumps(company)}
role: {json.dumps(role)}
location: {json.dumps(job.get('location') or '')}
salary: {json.dumps(job.get('salary') or '')}
contract: {json.dumps(job.get('contract') or '')}
posted: {json.dumps(job.get('posted') or '')}
imported: {datetime.now().strftime('%Y-%m-%d')}
evidence_level: full_jd
---

# {role} — {company}

- Source: Totaljobs
- Source URL: {job.get('url') or ''}
- Totaljobs job id: {job_id}
- Location: {job.get('location') or ''}
- Salary: {job.get('salary') or ''}
- Contract: {job.get('contract') or ''}
- Posted: {job.get('posted') or ''}

## Full job description

{(job.get('full_jd') or '').strip()}
"""
        if not dry_run:
            jds_dir.mkdir(parents=True, exist_ok=True)
            (jds_dir / jd_name).write_text(body, encoding="utf-8")
        entries.append(" | ".join([
            f"- [ ] local:{jd_rel}", company, role, job.get("location") or "", job.get("salary") or "",
            f"note: Totaljobs full JD import; source={job.get('url')}; job_id={job_id}",
        ]))
        imported += 1
    if not dry_run:
        pipeline_path.write_text(insert_pending(pipeline, entries), encoding="utf-8")
    print(json.dumps({"input": str(input_path), "career_ops": str(career_ops), "dry_run": dry_run, "imported_count": imported, "skipped_count": skipped}, indent=2))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan", "enrich", "export", "run"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--input")
    ap.add_argument("--limit", type=int, help="limit search pages for scan")
    ap.add_argument("--top", type=int)
    ap.add_argument("--delay", type=float)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="scan even when the board is disabled; for manual smoke tests")
    args = ap.parse_args()
    cfg = load_config(ROOT / args.config)
    if args.command == "scan":
        await scan_searches(cfg, args.limit, args.allow_disabled)
    elif args.command == "enrich":
        await enrich(cfg, Path(args.input) if args.input else None, args.top, args.delay)
    elif args.command == "export":
        export_to_career_ops(cfg, Path(args.input) if args.input else None, args.dry_run)
    elif args.command == "run":
        dedup = await scan_searches(cfg, args.limit, args.allow_disabled)
        enriched = await enrich(cfg, dedup, args.top, args.delay)
        export_to_career_ops(cfg, enriched, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
