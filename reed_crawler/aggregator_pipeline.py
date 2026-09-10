"""One board's worth of code, serving every aggregator feed.

An aggregator publishes many companies' postings from a single endpoint. Reading one is a
plain GET returning JSON or XML: no crawl4ai, no browser, no card parsing — the same shape
`adzuna_pipeline.py` established for an API board. Because that fetching is byte-identical
from feed to feed, it is written once here, and everything that actually differs lives in
`aggregator_feeds.FEEDS`.

"Duplicated by design" in CLAUDE.md is about *crawled* boards, whose markup is quirky in its
own way — a fix for Totaljobs must not be able to break Reed. These feeds have no markup and
no crawl config to diverge, so copying this file per feed would duplicate the part that is
identical while isolating nothing that varies. It would also mean that fixing a health
classification or a capture-naming bug is fourteen edits, and the one that gets missed is a
board that quietly stops honouring an invariant.

This module serves exactly the names in `aggregator_feeds.FEEDS`. It is not a framework: the
crawled boards are not refactored into it, now or later.

Terms: RemoteOK's own payload asks for a link back and Arbeitnow's `meta.terms` says the
same. This project keeps its reports private and republishes nothing, so reading them is in
bounds — but nothing in the code reveals that constraint exists, hence this paragraph.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from defusedxml import ElementTree

import aggregator_feeds
from aggregator_feeds import FEEDS
from board_config import build_board_urls, jittered, load_config, raw_capture_stem, run_stamp
import salary as salary_parser
import run_record
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"

TIMEOUT_SECONDS = 45

# These are public feeds that serve anything; the header set is ordinary politeness, and it
# is the request rate that matters. Accept-Encoding is added per feed.
HEADERS = {
    "accept": "application/json, application/xml;q=0.9, */*;q=0.8",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}


@dataclass
class AggregatorLead:
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


@dataclass
class Response:
    """What scan_health classifies. The feed's body plays the part the crawled page plays."""
    success: bool
    markdown: str = ""
    html: str = ""
    status_code: int | None = None
    error_message: str = ""


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def dedupe(leads: list[AggregatorLead]) -> list[AggregatorLead]:
    seen: dict[str, AggregatorLead] = {}
    for lead in leads:
        key = lead.job_id or lead.url or "|".join([lead.role_title.lower(), lead.company.lower()])
        if key not in seen:
            seen[key] = lead
    return list(seen.values())


def fetch(url: str, feed: aggregator_feeds.Feed) -> tuple[Response, object]:
    """One page. The decoded body is what scan_health sees, gzip or not."""
    headers = dict(HEADERS)
    if feed.gzip:
        headers["accept-encoding"] = "gzip"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            raw = reply.read()
            if (reply.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            body = raw.decode("utf-8", errors="replace")
            payload = ElementTree.fromstring(body) if feed.body == "xml" else json.loads(body)
            return Response(True, markdown=body, status_code=reply.status), payload
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as failure:
        return Response(False, status_code=getattr(failure, "code", None),
                        error_message=str(failure)), None


def leads_from(payload: object, spec: dict, feed: aggregator_feeds.Feed) -> tuple[list[AggregatorLead], list]:
    """The page's records as leads, and the raw records so the caller can ask for more."""
    rows = feed.rows(payload)
    leads = []
    for row in rows:
        fields = feed.lead(row, feed.label)
        if not fields:
            continue
        lead = AggregatorLead(
            source=feed.name,
            search_title=spec["title"],
            search_location=spec["location"],
            **{k: v for k, v in fields.items()},
        )
        # A feed that states pay as numbers has already set them; only prose needs parsing.
        if lead.salary and lead.salary_min is None and lead.salary_max is None:
            salary_parser.apply_to(lead)
        leads.append(lead)
    return leads, rows


def scan(feed_name: str, cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    feed = FEEDS[feed_name]
    board = (cfg.get("boards") or {}).get(feed_name) or {}
    if not board.get("enabled") and not allow_disabled:
        raise SystemExit(
            f"{feed_name} is disabled in config.yml. Use --allow-disabled for manual smoke tests."
        )
    specs = build_board_urls(
        {**cfg, "boards": {**cfg.get("boards", {}), feed_name: {**board, "enabled": True}}},
        feed_name,
    )
    if limit:
        specs = specs[:limit]

    out = OUTPUTS / feed_name
    raw_dir, reports = out / "raw", out / "reports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages_per_query = max(1, int(board.get("pages_per_query", 1)))
    max_items = int(board.get("max_items", 0))
    delay = float(board.get("delay_seconds", (cfg.get("crawl") or {}).get("delay_seconds", 15)))

    with scan_lock.hold(feed_name):
        stamp = run_stamp()
        with run_record.record(feed_name, stamp) as findings:
            health = scan_health.RunHealth(feed_name)
            all_leads: list[AggregatorLead] = []
            requests = 0
            for spec in specs:
                query = "" if spec["title"] == feed.label else spec["title"]
                for page in range(1, pages_per_query + 1):
                    url = spec["url"] if page == 1 else feed.page_url(query, spec["location"], page, board)
                    print(f"Querying {feed.label} {spec['title']!r} page {page}: {url}")
                    response, payload = fetch(url, feed)
                    requests += 1
                    parts = [slug(spec["title"])]
                    if spec["location"]:
                        parts.append(slug(spec["location"]))
                    if page > 1:
                        parts.append(f"p{page}")
                    stem = raw_capture_stem("__".join(parts), stamp)
                    (raw_dir / f"{stem}.{feed.capture_ext}").write_text(
                        response.markdown or "", encoding="utf-8")
                    outcome = health.record(response)
                    if outcome != scan_health.OK:
                        print(f"  {outcome} status={response.status_code} error={response.error_message}")
                        break
                    leads, rows = leads_from(payload, spec, feed)
                    print(f"  {outcome} status={response.status_code} records={len(rows)} leads={len(leads)}")
                    all_leads.extend(leads)
                    # Never request the page after the last one: an honestly empty reply is
                    # short enough to classify as empty-body, which would read as a broken
                    # board rather than as a finished search.
                    if not feed.more(payload, page, rows):
                        break
                    time.sleep(jittered(delay))
                time.sleep(jittered(delay))

            deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
            if max_items:
                deduped = deduped[:max_items]
            reports.mkdir(parents=True, exist_ok=True)
            raw_path = reports / f"{feed_name}_raw_{stamp}.json"
            dedup_path = reports / f"{feed_name}_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
            dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
            print(f"{feed.label} raw={len(all_leads)} deduped={len(deduped)}")
            findings.update(jobs=len(deduped), searches=len(specs))
            print(f"Deduped JSON: {dedup_path}")
            health.finish()
            return dedup_path


def main() -> None:
    ap = argparse.ArgumentParser(description="scan one aggregator feed")
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--feed", required=True, choices=sorted(FEEDS))
    ap.add_argument("--config", default=str(ROOT / "config.yml"))
    ap.add_argument("--limit", type=int, default=None, help="only the first N queries")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="scan even when the board is disabled; for manual smoke tests")
    args = ap.parse_args()
    scan(args.feed, load_config(Path(args.config)), args.limit, args.allow_disabled)


if __name__ == "__main__":
    main()
