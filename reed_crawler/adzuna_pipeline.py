"""Collect Adzuna listings from Adzuna's own API.

Adzuna's search pages sit behind CloudFront, which answers any automated fetch with a bare
403 — curl and a headless Chromium alike, whatever user agent is offered. There is no markup
to parse because the markup never arrives.

Adzuna publishes a free JSON search API instead, so this board reads that. It is the only
board here that needs no browser: no crawl4ai, no card parsing, no anti-detection, and
salaries arrive as numbers rather than as advertiser prose. It still writes the same raw
captures and the same report shape as every other board, so nothing downstream can tell the
difference.

Credentials are free from developer.adzuna.com and live in config.yml (gitignored) under
boards.adzuna, or in ADZUNA_APP_ID / ADZUNA_APP_KEY. They are attached at request time and
never written to a capture, a report or the log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from board_config import build_board_urls, load_config, jittered, raw_capture_stem, run_stamp
from lead import Lead, dedupe, slug
import salary as salary_parser
import run_record
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "adzuna"
RAW = OUT / "raw"
REPORTS = OUT / "reports"

TIMEOUT_SECONDS = 30


def credentials(cfg: dict) -> tuple[str, str]:
    """The API identity, from the environment first so a shared config need not hold secrets."""
    board = (cfg.get("boards") or {}).get("adzuna") or {}
    app_id = os.environ.get("ADZUNA_APP_ID") or str(board.get("app_id") or "")
    app_key = os.environ.get("ADZUNA_APP_KEY") or str(board.get("app_key") or "")
    if not app_id or not app_key:
        raise SystemExit(
            "Adzuna needs an app_id and app_key. Register free at developer.adzuna.com, then set "
            "boards.adzuna.app_id / app_key in config.yml or ADZUNA_APP_ID / ADZUNA_APP_KEY."
        )
    return app_id, app_key


@dataclass
class Response:
    """What scan_health classifies. The API's body plays the part the crawled page plays."""
    success: bool
    markdown: str = ""
    html: str = ""
    status_code: int | None = None
    error_message: str = ""


def fetch(url: str, app_id: str, app_key: str) -> tuple[Response, dict]:
    """One search request. Credentials go on here and nowhere else."""
    authed = f"{url}&{urlencode({'app_id': app_id, 'app_key': app_key})}"
    try:
        with urllib.request.urlopen(authed, timeout=TIMEOUT_SECONDS) as reply:
            body = reply.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            return Response(True, markdown=body, status_code=reply.status), payload
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as failure:
        status = getattr(failure, "code", None)
        # The message can quote the request; strip the key rather than log it.
        return Response(False, status_code=status, error_message=redact(str(failure), app_key)), {}


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def salary_text(job: dict) -> str:
    """Adzuna states pay as numbers, so the text is ours to write rather than to parse.

    A predicted salary is Adzuna's estimate, not the advertiser's claim, and says so.
    """
    low, high = job.get("salary_min"), job.get("salary_max")
    if not low and not high:
        return ""
    low, high = int(low or high), int(high or low)
    amount = f"£{low:,}" if low == high else f"£{low:,} - £{high:,}"
    estimated = " (Adzuna estimate)" if str(job.get("salary_is_predicted") or "0") == "1" else ""
    return f"{amount} per year{estimated}"


def contract_text(job: dict) -> str:
    parts = [str(job.get(k) or "").replace("_", " ").strip() for k in ("contract_type", "contract_time")]
    return ", ".join(p.capitalize() for p in parts if p)


def parse_results(payload: dict, spec: dict) -> list[Lead]:
    leads = []
    for job in payload.get("results") or []:
        jid = str(job.get("id") or "")
        if not jid:
            continue
        low, high = job.get("salary_min"), job.get("salary_max")
        leads.append(Lead(
            source="adzuna",
            search_title=spec["title"],
            search_location=spec["location"],
            role_title=job.get("title") or "Unknown role",
            company=(job.get("company") or {}).get("display_name") or "",
            salary=salary_text(job),
            location=(job.get("location") or {}).get("display_name") or "",
            contract=contract_text(job),
            posted=str(job.get("created") or "")[:10],
            url=job.get("redirect_url") or "",
            job_id=jid,
            raw_block=(job.get("description") or "")[:600],
            # Structured at the source: parsing our own generated text back would only lose precision.
            salary_min=int(low) if low else None,
            salary_max=int(high) if high else None,
            salary_period="year" if (low or high) else "",
        ))
    return leads


def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False) -> Path:
    board = (cfg.get("boards") or {}).get("adzuna") or {}
    if not board.get("enabled") and not allow_disabled:
        raise SystemExit("Adzuna is disabled in config.yml. Use --allow-disabled for manual smoke tests.")
    app_id, app_key = credentials(cfg)
    specs = build_board_urls({**cfg, "boards": {**cfg.get("boards", {}), "adzuna": {**board, "enabled": True}}}, "adzuna")
    if limit:
        specs = specs[:limit]
    RAW.mkdir(parents=True, exist_ok=True)
    with scan_lock.hold("adzuna"):
        stamp = run_stamp()
        with run_record.record("adzuna", stamp) as findings:
            health = scan_health.RunHealth("adzuna")
            all_leads: list[Lead] = []
            for spec in specs:
                print(f"Querying Adzuna {spec['title']!r} / {spec['location']!r}: {spec['url']}")
                response, payload = fetch(spec["url"], app_id, app_key)
                stem = raw_capture_stem(f"{slug(spec['title'])}__{slug(spec['location'])}", stamp)
                (RAW / f"{stem}.json").write_text(response.markdown or "", encoding="utf-8")
                outcome = health.record(response)
                if outcome != scan_health.OK:
                    print(f"  {outcome} status={response.status_code} error={response.error_message}")
                else:
                    leads = parse_results(payload, spec)
                    print(f"  {outcome} status={response.status_code} matches={payload.get('count')} leads={len(leads)}")
                    all_leads.extend(leads)
                time.sleep(jittered(float((cfg.get("crawl") or {}).get("delay_seconds", 15))))

            deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
            REPORTS.mkdir(parents=True, exist_ok=True)
            raw_path = REPORTS / f"adzuna_raw_{stamp}.json"
            dedup_path = REPORTS / f"adzuna_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
            dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")
            print(f"Adzuna raw={len(all_leads)} deduped={len(deduped)}")
            findings.update(jobs=len(deduped), searches=len(specs))
            print(f"Deduped JSON: {dedup_path}")
            health.finish()
            return dedup_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--allow-disabled", action="store_true", help="manual smoke test even when boards.adzuna.enabled=false")
    args = ap.parse_args()
    scan(load_config(ROOT / args.config), args.limit, args.allow_disabled)


if __name__ == "__main__":
    main()
