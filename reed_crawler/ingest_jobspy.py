"""Fold collected listings into the downstream career-ops pipeline.

Ported from career-ops/ingest-jobspy.mjs, which folded a JobSpy CSV export into
`data/pipeline.md`. It reads this repo's own deduped reports as well now, so the chain the
email board completes — alert mail in, leads out — ends where every other lead ends, and
`ingest_jobspy.py email_deduped_*.json` needs no CSV in the middle.

What it is for is filtering. A report is everything a board showed; the pipeline is what is
worth opening. Relevance is defined once, downstream, in career-ops/portals.yml — the same
`title_filter` and `location_filter` its own scanner uses — so this project never gets a
second opinion about what a relevant job is.

This is the one module here that writes into the downstream workspace, which is otherwise
read-only (see `dashboard/pipeline.py`). That is why it is a terminal command, run against a
report you have looked at, and why it is deliberately absent from `scan_all.COMMANDS` and
from the dashboard's buttons: a scan collects, a person ingests.

Usage:
  .venv/bin/python reed_crawler/ingest_jobspy.py outputs/email/reports/email_deduped_*.json
  .venv/bin/python reed_crawler/ingest_jobspy.py --latest email --dry-run
  .venv/bin/python reed_crawler/ingest_jobspy.py jobspy-export.csv --max-age-days 30
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_AGE_DAYS = 45

# JobSpy and the alert mail both surface trades, construction and finance titles that share
# our seniority words ("Lead", "Senior") but none of the domain. portals.yml's negative list is
# tuned for ATS boards and does not carry these.
EXTRA_NEGATIVE = [
    "mep", "civils", "structures", "maintenance technician", "field service",
    "optical", "reporting lead", "quantity surveyor", "site manager", "hgv",
    "nurse", "teacher", "chef", "driver", "electrician", "mechanical",
]

# career-ops writes its own pipeline in Spanish and English by turns; both name the same section.
PENDING_HEADING = re.compile(r"^##[ \t]+(Pending|Pendientes)[ \t]*$", re.M | re.I)
URL_IN_LINE = re.compile(r"(https?://\S+)")


def workspace(override: str = "") -> Path:
    """Where the downstream workspace lives. Resolution matches the dashboard's."""
    configured = ""
    config_path = ROOT / "config.yml"
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            configured = (config.get("career_ops") or {}).get("workspace") or ""
        except (OSError, yaml.YAMLError):
            configured = ""
    candidate = override or os.environ.get("CAREER_OPS_WORKSPACE") or configured or (ROOT.parent / "career-ops")
    path = Path(candidate)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not (path / "data" / "pipeline.md").exists():
        raise SystemExit(f"No career-ops workspace at {path} (expected data/pipeline.md there).")
    return path


class Filters:
    """portals.yml's definition of a relevant job, applied to one row."""

    def __init__(self, portals: dict):
        titles = portals.get("title_filter") or {}
        locations = portals.get("location_filter") or {}
        self.positive = [s.lower() for s in titles.get("positive") or []]
        self.negative = [s.lower() for s in titles.get("negative") or []] + EXTRA_NEGATIVE
        self.always_allow = [s.lower() for s in locations.get("always_allow") or []]
        self.allow = [s.lower() for s in locations.get("allow") or []]
        self.block = [s.lower() for s in locations.get("block") or []]

    def title_passes(self, title: str) -> bool:
        t = (title or "").lower()
        if any(n in t for n in self.negative):
            return False
        return any(p in t for p in self.positive)

    def location_passes(self, location: str) -> bool:
        l = (location or "").lower()
        if not l:
            return True                     # do not penalise missing data
        if any(a in l for a in self.always_allow):
            return True
        if any(b in l for b in self.block):
            return False
        return not self.allow or any(a in l for a in self.allow)


def load_filters(base: Path) -> Filters:
    path = base / "portals.yml"
    if not path.exists():
        raise SystemExit(f"No portals.yml at {path}; that file defines what counts as relevant.")
    return Filters(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def read_rows(path: Path) -> tuple[list[dict], str]:
    """Rows in one shape, whichever shape they arrived in, plus where they came from.

    A deduped report from this project and a JobSpy CSV name the same things differently;
    everything below this line sees one vocabulary.
    """
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            raw = list(csv.DictReader(handle))
        rows = [{
            "url": (r.get("job_url") or "").strip(),
            "board": "",
            "job_id": "",
            "title": (r.get("title") or "").strip(),
            "company": (r.get("company") or "").strip(),
            "location": (r.get("location") or "").strip(),
            "posted": (r.get("date_posted") or "").strip(),
            "site": (r.get("site") or "jobspy").strip(),
        } for r in raw]
        return rows, "jobspy"

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a report: expected a JSON list of leads.")
    board = path.name.split("_")[0]
    rows = [{
        # A report URL is written by a board's own parser; take the href and nothing else.
        "url": (r.get("url") or "").strip().split(" ", 1)[0],
        "board": board,
        "job_id": (r.get("job_id") or "").strip(),
        "title": (r.get("role_title") or "").strip(),
        "company": (r.get("company") or "").strip(),
        "location": (r.get("location") or "").strip(),
        "posted": (r.get("posted") or "").strip(),
        "site": (r.get("source") or path.name.split("_")[0]).strip(),
    } for r in data]
    return rows, "crawler"


def latest_report(board: str) -> Path:
    reports = sorted((ROOT / "outputs" / board / "reports").glob(f"{board}_deduped_*.json"))
    if not reports:
        raise SystemExit(f"No deduped report for {board}; scan it first.")
    return reports[-1]


def known_urls(base: Path) -> set[str]:
    """Everything already seen: the pipeline inbox, and the scanner's own dedup ledger."""
    found = set()
    for line in (base / "data" / "pipeline.md").read_text(encoding="utf-8").splitlines():
        match = URL_IN_LINE.search(line)
        if match:
            found.add(match.group(1))
    history = base / "data" / "scan-history.tsv"
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            url = line.split("\t")[0]
            if url.startswith("http"):
                found.add(url)
    return found


def iso_date(posted: str) -> date | None:
    """The date a row states, or None when it states prose ("4 days ago") or nothing."""
    try:
        return datetime.fromisoformat((posted or "")[:10]).date()
    except ValueError:
        return None


def stale(posted: str, max_age_days: int) -> bool:
    when = iso_date(posted)
    if when is None:
        return False                        # a row that states no date is not evidence of age
    return when < date.today() - timedelta(days=max_age_days)


def select(rows: list[dict], filters: Filters, seen: set[str], max_age_days: int) -> tuple[list[dict], dict]:
    stats = {"total": len(rows), "title": 0, "location": 0, "stale": 0,
             "known": 0, "same_req": 0, "kept": 0}
    reqs: set[str] = set()
    kept = []
    for row in rows:
        if not row["url"].startswith("http"):
            continue
        if not filters.title_passes(row["title"]):
            stats["title"] += 1
            continue
        if not filters.location_passes(row["location"]):
            stats["location"] += 1
            continue
        if stale(row["posted"], max_age_days):
            stats["stale"] += 1
            continue
        if row["url"] in seen:
            stats["known"] += 1
            continue
        # One req posted to several cities arrives as several rows, under several URLs.
        req = f"{row['company'].lower()}|{row['title'].lower()}"
        if req in reqs:
            stats["same_req"] += 1
            continue
        reqs.add(req)
        seen.add(row["url"])
        stats["kept"] += 1
        kept.append(row)
    return kept, stats


def pipeline_line(row: dict) -> str:
    # The id travels with the entry. A sponsored Indeed link is a different URL in every mail,
    # so a URL alone cannot tell the dashboard that this job is already in the pipeline.
    tail = f" | job_id={row['board']}-{row['job_id']}" if row.get("job_id") else ""
    tail += f" | {row['location']}" if row["location"] else ""
    # Crawled boards state "4 days ago" or "25 July", which is only meaningful on the day it
    # was scraped. A permanent file gets a date it can still be read against, or nothing.
    tail += f" | posted: {row['posted']}" if iso_date(row["posted"]) else ""
    return f"- [ ] {row['url']} | {row['company'] or 'unknown'} | {row['title']}{tail}"


def history_line(row: dict, origin: str, today: str) -> str:
    return "\t".join([row["url"], today, f"{origin}-{row['site']}", row["title"],
                      row["company"] or "unknown", "added", row["location"]])


def insert_into_pending(path: Path, lines: list[str]) -> None:
    """Append at the end of the Pending section, not the end of the file.

    Appending blindly buries new entries under the processed section, where career-ops'
    pipeline mode never looks. Mirrors appendToPipeline() in its scan.mjs.

    ponytail: no lock. This is a terminal command run by hand, one at a time.
    """
    text = path.read_text(encoding="utf-8")
    block = "\n".join(lines) + "\n"
    heading = PENDING_HEADING.search(text)
    if not heading:
        path.write_text(re.sub(r"\n*$", "\n", text) + "\n## Pending\n\n" + block, encoding="utf-8")
        return
    following = text.find("\n## ", heading.end())
    cut = len(text) if following == -1 else following
    path.write_text(re.sub(r"\n*$", "\n", text[:cut]) + block + text[cut:], encoding="utf-8")


def ingest(report: Path, base: Path, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
           dry_run: bool = False) -> list[dict]:
    rows, origin = read_rows(report)
    kept, stats = select(rows, load_filters(base), known_urls(base), max_age_days)

    print(f"{report.name}: rows={stats['total']} -> kept={stats['kept']}")
    print(f"  dropped: title={stats['title']} location={stats['location']} "
          f"stale>{max_age_days}d={stats['stale']} known={stats['known']} same-req={stats['same_req']}")

    if dry_run:
        for row in kept:
            print(pipeline_line(row))
        print("\n(dry run — nothing written)")
        return kept
    if not kept:
        print("\nNothing to add.")
        return kept

    today = date.today().isoformat()
    insert_into_pending(base / "data" / "pipeline.md", [pipeline_line(r) for r in kept])
    history = base / "data" / "scan-history.tsv"
    with history.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(history_line(r, origin, today) for r in kept) + "\n")
    print(f"\nAppended {len(kept)} to {history.parent.name}/pipeline.md and scan-history.tsv")
    return kept


def main() -> None:
    ap = argparse.ArgumentParser(description="fold a report or a JobSpy CSV into career-ops")
    ap.add_argument("report", nargs="?", help="a deduped report JSON, or a JobSpy CSV export")
    ap.add_argument("--latest", metavar="BOARD", help="use that board's newest deduped report")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument("--workspace", default="", help="career-ops checkout; defaults to the configured one")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added, write nothing")
    args = ap.parse_args()
    if not args.report and not args.latest:
        ap.error("give a report path or --latest BOARD")
    report = latest_report(args.latest) if args.latest else Path(args.report)
    if not report.is_absolute():
        report = (Path.cwd() / report).resolve()
    ingest(report, workspace(args.workspace), args.max_age_days, args.dry_run)


if __name__ == "__main__":
    main()
