"""Derive the current state of the crawl from the report files on disk.

There is no stored index. Everything here is recomputed per request from the deduped report
JSON each scan writes, so the dashboard can never disagree with what the crawler actually
produced.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
BOARDS = ["reed", "totaljobs", "indeed", "talent", "adzuna", "haystack", "linkedin", "email"]

_STAMP = re.compile(r"_(\d{4}-\d{2}-\d{2}_\d{6})\.json$")

# Fields worth carrying onto the aggregated job; anything else stays in the report file.
_CARRIED = (
    "role_title", "company", "location", "salary", "salary_min", "salary_max",
    "salary_period", "contract", "posted", "url", "search_title", "search_location",
)


@dataclass
class Job:
    board: str
    job_id: str
    first_seen: str
    last_seen: str
    times_seen: int
    fields: dict = field(default_factory=dict)
    pipeline: object = None  # set by dashboard.pipeline.annotate when the workspace exists

    def get(self, name, default=""):
        return self.fields.get(name) or default

    @property
    def role_title(self) -> str:
        return self.get("role_title", "Unknown role")

    @property
    def company(self) -> str:
        return self.get("company", "")

    @property
    def pay(self) -> int | None:
        return self.fields.get("salary_max") or self.fields.get("salary_min")


@dataclass
class BoardSummary:
    board: str
    scanned: bool = False
    known: int = 0
    new: int = 0
    runs: int = 0
    last_run: str = ""
    last_run_jobs: int = 0

    @property
    def last_run_display(self) -> str:
        return format_stamp(self.last_run) if self.last_run else "never"


def deduped_reports(board: str, outputs: Path = OUTPUTS) -> list[tuple[str, Path]]:
    """Every deduped report for a board, oldest first, paired with its run stamp."""
    found = []
    for path in sorted((outputs / board / "reports").glob(f"{board}_deduped_*.json")):
        match = _STAMP.search(path.name)
        if match:
            found.append((match.group(1), path))
    return found


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def jobs_for_board(board: str, outputs: Path = OUTPUTS) -> list[Job]:
    """Collapse every run of a board into one record per job."""
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    times: Counter = Counter()
    merged: dict[str, dict] = defaultdict(dict)

    for stamp, path in deduped_reports(board, outputs):
        for row in _read(path):
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            first[job_id] = min(first.get(job_id, stamp), stamp)
            last[job_id] = max(last.get(job_id, ""), stamp)
            times[job_id] += 1
            # Later runs win, but a later blank never erases a value an earlier run captured.
            for key in _CARRIED:
                value = row.get(key)
                if value not in (None, ""):
                    merged[job_id][key] = value

    return [
        Job(
            board=board,
            job_id=job_id,
            first_seen=first[job_id],
            last_seen=last[job_id],
            times_seen=times[job_id],
            fields=merged[job_id],
        )
        for job_id in sorted(first, key=lambda j: (first[j], j), reverse=True)
    ]


def summarise_board(board: str, outputs: Path = OUTPUTS) -> BoardSummary:
    reports = deduped_reports(board, outputs)
    if not reports:
        return BoardSummary(board=board)

    jobs = jobs_for_board(board, outputs)
    last_run = max(stamp for stamp, _ in reports)
    return BoardSummary(
        board=board,
        scanned=True,
        known=len(jobs),
        new=sum(1 for j in jobs if j.first_seen == last_run),
        runs=len(reports),
        last_run=last_run,
        last_run_jobs=len(_read(dict(reports)[last_run])),
    )


def summarise_all(outputs: Path = OUTPUTS) -> list[BoardSummary]:
    return [summarise_board(board, outputs) for board in BOARDS]


def format_stamp(stamp: str) -> str:
    """Render a run stamp for a human.

    Tolerates the shape records used before scans carried the report stamp, so old rows still
    read as dates rather than as identifiers.
    """
    digits = "".join(c for c in stamp if c.isdigit())
    if len(digits) >= 12:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}"
    return stamp


@dataclass
class Run:
    board: str
    stamp: str
    jobs: int
    searches: int
    healthy: bool
    status: str = ""
    trigger: str = ""
    run_id: str = ""
    has_log: bool = False

    @property
    def display_time(self) -> str:
        return format_stamp(self.stamp)


def runs(board: str = "", outputs: Path = OUTPUTS, recorded: list | None = None) -> list[Run]:
    """Every scan, most recent first, whatever started it.

    Two things are known about a scan and neither is sufficient alone. The report files say
    what was found, but a scan that failed wrote none. The run records say how a scan ended,
    but only exist for scans run since that was added. So they are merged on (board, stamp).
    """
    found: dict[tuple, Run] = {}
    for name in ([board] if board else BOARDS):
        for stamp, path in deduped_reports(name, outputs):
            rows = _read(path)
            searches = {(r.get("search_title"), r.get("search_location")) for r in rows}
            found[(name, stamp)] = Run(board=name, stamp=stamp, jobs=len(rows),
                                       searches=len(searches), healthy=bool(rows))

    for record in (recorded if recorded is not None else _recorded_runs()):
        name = record.board
        stamp = record.id[:-(len(name) + 1)] if record.id.endswith(f"-{name}") else record.id
        if board and name != board:
            continue
        existing = found.get((name, stamp))
        if existing:
            existing.status = record.status
            existing.trigger = record.trigger
            existing.run_id = record.id
            existing.has_log = record.has_log
        else:
            # A scan that produced no report at all — which is the interesting kind.
            found[(name, stamp)] = Run(
                board=name, stamp=stamp,
                jobs=record.jobs or 0, searches=record.searches or 0,
                healthy=record.status == "done" and bool(record.jobs),
                status=record.status, trigger=record.trigger,
                run_id=record.id, has_log=record.has_log,
            )
    # Sort on the digits: stamp shapes differ across the history, and "-" and "0" do not
    # compare the way the dates they separate do.
    return sorted(found.values(), key=lambda r: "".join(c for c in r.stamp if c.isdigit()), reverse=True)


def _recorded_runs() -> list:
    # Reading a run is a file read, so it does not matter which process started the scan.
    from runner import scans
    return scans.history(10_000)


def page_of(items: list, page: int, per_page: int) -> tuple[list, int, int]:
    """Slice a list into a page, returning the slice, the clamped page and the page count."""
    pages = max(1, -(-len(items) // per_page))
    page = min(max(page, 1), pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, pages


def all_jobs(outputs: Path = OUTPUTS) -> list[Job]:
    jobs: list[Job] = []
    for board in BOARDS:
        jobs.extend(jobs_for_board(board, outputs))
    return jobs


def find_job(board: str, job_id: str, outputs: Path = OUTPUTS) -> Job | None:
    for job in jobs_for_board(board, outputs):
        if job.job_id == job_id:
            return job
    return None


def sightings(board: str, job_id: str, outputs: Path = OUTPUTS) -> list[str]:
    """Every run stamp in which this job was seen, most recent first."""
    seen = []
    for stamp, path in deduped_reports(board, outputs):
        if any(str(row.get("job_id") or "") == job_id for row in _read(path)):
            seen.append(stamp)
    return sorted(seen, reverse=True)


SORTS = {
    "first_seen": lambda j: j.first_seen,
    "last_seen": lambda j: j.last_seen,
    "pay": lambda j: (j.pay is not None, j.pay or 0),
    "company": lambda j: (j.company or "￿").lower(),
    "role": lambda j: j.role_title.lower(),
    "times_seen": lambda j: j.times_seen,
}

# Ascending reads more naturally for names; everything else is most-interesting-first.
_ASCENDING = {"company", "role"}


def select(jobs: list[Job], board: str = "", min_pay: int | None = None,
           query: str = "", sort: str = "first_seen", actioned: str = "") -> list[Job]:
    """Apply the filters and ordering the job list offers."""
    chosen = jobs
    if actioned == "no":
        chosen = [j for j in chosen if not (j.pipeline and j.pipeline.present)]
    elif actioned == "yes":
        chosen = [j for j in chosen if j.pipeline and j.pipeline.present]
    if board:
        chosen = [j for j in chosen if j.board == board]
    if min_pay:
        # An advert with no figure cannot be shown to clear a floor, so it is excluded.
        chosen = [j for j in chosen if (j.pay or 0) >= min_pay]
    if query:
        needle = query.lower()
        chosen = [j for j in chosen
                  if needle in j.role_title.lower()
                  or needle in j.company.lower()
                  or needle in str(j.get("location")).lower()]
    key = SORTS.get(sort, SORTS["first_seen"])
    return sorted(chosen, key=key, reverse=sort not in _ASCENDING)


def totals(summaries: list[BoardSummary]) -> dict:
    return {
        "known": sum(s.known for s in summaries),
        "new": sum(s.new for s in summaries),
        "runs": sum(s.runs for s in summaries),
    }
