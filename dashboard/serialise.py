"""Turn the dashboard's dataclasses into the JSON the client renders.

One module so a field only ever gains a name once. The client is TypeScript and its types are
written from what is here; nothing else in the package builds a response body by hand.
"""
from __future__ import annotations

from dataclasses import asdict

from . import aggregate


def job(item: aggregate.Job, *, detail: bool = False) -> dict:
    """One job, flattened.

    The report's own fields are kept in their own object rather than merged into the row: they
    vary by board, and a board that starts reporting a field called `board` should not be able
    to overwrite which board found it.
    """
    body = {
        "board": item.board,
        "job_id": item.job_id,
        "role_title": item.role_title,
        "company": item.company,
        "location": item.get("location"),
        "salary": item.get("salary"),
        "salary_min": item.fields.get("salary_min"),
        "salary_max": item.fields.get("salary_max"),
        "salary_period": item.get("salary_period"),
        "pay": item.pay,
        "url": item.get("url"),
        "posted": item.get("posted"),
        "first_seen": item.first_seen,
        "last_seen": item.last_seen,
        "times_seen": item.times_seen,
        "ingest_skip": item.ingest_skip,
        "pipeline": {
            "present": bool(item.pipeline and item.pipeline.present),
            "done": bool(item.pipeline and item.pipeline.done),
        },
    }
    if detail:
        body |= {
            "contract": item.get("contract"),
            "search_title": item.get("search_title"),
            "search_location": item.get("search_location"),
        }
    return body


def summary(item: aggregate.BoardSummary) -> dict:
    return asdict(item) | {"last_run_display": item.last_run_display}


def run(item: aggregate.Run) -> dict:
    return asdict(item) | {"display_time": item.display_time}


def scan_run(item) -> dict:
    """A `runner.scans.ScanRun` — the record of a scan this dashboard can follow."""
    return asdict(item) | {"has_log": item.has_log, "display_started": item.display_started}


def schedule_row(item) -> dict:
    return asdict(item)


def preview(item) -> dict:
    return {
        "board": item.board,
        "report": item.report,
        "stamp": item.stamp,
        "count": item.count,
        "error": item.error,
    }


def candidate(item) -> dict:
    return {
        "board": item.board,
        "title": item.title,
        "company": item.company,
        "location": item.location,
        "posted": item.posted,
        "url": item.url,
        "line": item.line,
    }
