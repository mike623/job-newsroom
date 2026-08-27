"""Read-only view of the downstream workspace.

Of the jobs currently live on a board, most have already been dealt with. Without knowing
which, the job list cannot answer the question actually worth asking: what is live that has
not been actioned yet.

Nothing here writes. The workspace belongs to another project with its own tooling, and this
is only ever a lookup.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import email_pipeline

_ENTRY = re.compile(r"^-\s*\[(?P<tick>[ xX])\](?P<body>.*)$", re.M)

# Entries reach the pipeline from several places, so an id has to be recovered from whichever
# shape a line happens to use. Most are plain URLs pasted in by hand; a minority are the
# crawler's own "local:jds/<board>-<id>-..." imports.
_IDENTIFIERS = [
    ("reed", re.compile(r"reed\.co\.uk/jobs/[^/\s]+/(\d+)")),
    ("totaljobs", re.compile(r"totaljobs\.com/job/(?:[^\s|]*?job)?(\d+)")),
    ("indeed", re.compile(r"indeed\.com/\S*?[?&]jk=([A-Za-z0-9]+)")),
    ("talent", re.compile(r"talent\.com/view\?id=(\d+)")),
    # Adzuna hands back two link shapes for the same advert: /jobs/details/<id> and a
    # /land/ad/<id> click wrapper. Both carry the id the API reports.
    ("adzuna", re.compile(r"adzuna\.co\.uk/(?:jobs/)?(?:details|land/ad)/(\d+)")),
    # Haystack ids are UUIDs, so the hyphen-delimited "local:jds/<board>-<id>-" shape below
    # cannot recover one; the URL is what identifies a Haystack advert.
    ("haystack", re.compile(r"haystack\.cv/jobs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")),
]
_LOCAL_IMPORT = re.compile(r"local:jds/([a-z]+)-([^-\s]+)-")
_URL = re.compile(r"https?://[^\s|]+")
# `ingest_jobspy` states the id it filed an entry under; a sponsored Indeed link is a
# different URL in every mail, so the URL alone cannot always identify the job.
_STATED_ID = re.compile(r"job_id=([a-z]+)-(\S+)")


def _identify(line: str) -> set[tuple[str, str]]:
    """Every (board, job_id) a pipeline line refers to.

    The email board has no URL shape of its own: it forwards LinkedIn, Indeed, Totaljobs and
    Jobright postings, and it is the id it derived from that URL that names the job here. So
    the recognition is asked of the board itself rather than restated as another regex — one
    definition of an email lead's id, in `email_pipeline`.
    """
    found = {(board, m.group(1)) for board, pattern in _IDENTIFIERS for m in pattern.finditer(line)}
    found |= {(m.group(1), m.group(2)) for m in _LOCAL_IMPORT.finditer(line)}
    found |= {(m.group(1), m.group(2)) for m in _STATED_ID.finditer(line)}
    for url in _URL.findall(line):
        job_id = email_pipeline.job_id_from_url(url)
        if job_id:
            found.add(("email", job_id))
    return found


@dataclass(frozen=True)
class Status:
    present: bool = False
    done: bool = False


ABSENT = Status()


def workspace() -> Path | None:
    """Where the downstream workspace lives, or None if it is not there.

    Resolution matches the crawler's: the environment wins, then config, then a sibling
    directory.
    """
    configured = ""
    config_path = ROOT / "config.yml"
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            configured = (config.get("career_ops") or {}).get("workspace") or ""
        except (OSError, yaml.YAMLError):
            configured = ""

    candidate = os.environ.get("CAREER_OPS_WORKSPACE") or configured or (ROOT.parent / "career-ops")
    path = Path(candidate)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path if (path / "data" / "pipeline.md").exists() else None


def load(root: Path | None = None) -> dict[tuple[str, str], Status]:
    """Map (board, job_id) to its downstream status.

    Returns empty when the workspace is missing or unreadable, so the column simply hides
    rather than the page failing.
    """
    base = root if root is not None else workspace()
    if base is None:
        return {}
    try:
        text = (base / "data" / "pipeline.md").read_text(encoding="utf-8")
    except OSError:
        return {}

    found: dict[tuple[str, str], Status] = {}
    for match in _ENTRY.finditer(text):
        done = match.group("tick").lower() == "x"
        for key in _identify(match.group("body")):
            # A job can be listed more than once; treat it as actioned if any entry is ticked.
            previous = found.get(key)
            found[key] = Status(present=True, done=done or bool(previous and previous.done))
    return found


def annotate(jobs, statuses: dict[tuple[str, str], Status]) -> None:
    """Attach downstream status to each job, in place."""
    for job in jobs:
        job.pipeline = statuses.get((job.board, job.job_id), ABSENT)
