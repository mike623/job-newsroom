"""Read and edit the crawler's timetable.

The schedule itself belongs to `reed_crawler/schedule.py`, which is what the timer consults;
this only turns it into rows a page can render and turns a submitted form back into entries.
Validation is not repeated here — a rule enforced in two places is a rule that disagrees with
itself eventually.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yml"
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config
import install_timer
import run_record
import schedule


@dataclass
class Submitted:
    """A posted form, read with the standard library.

    Starlette's own `request.form()` requires python-multipart even for a plain urlencoded
    body. These pages never upload a file, so a dependency would buy nothing.
    """
    values: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def parse(cls, body: bytes) -> "Submitted":
        return cls(parse_qs(body.decode("utf-8"), keep_blank_values=True))

    def get(self, key: str, default: str = "") -> str:
        found = self.values.get(key) or []
        return found[0] if found else default

    def getlist(self, key: str) -> list[str]:
        return list(self.values.get(key) or [])


@dataclass
class Row:
    """One board's timetable, as the page shows it."""
    board: str
    enabled: bool = False
    mode: str = "at"                  # "at" or "every"
    at: str = ""                      # comma-separated HH:MM
    every_minutes: str = ""
    window_from: str = ""
    window_to: str = ""
    days: list[int] = None
    runnable: bool = True             # enabled in config.yml
    summary: str = "off"
    next_due: str = ""
    last_run: str = ""
    overdue: bool = False

    def __post_init__(self):
        self.days = self.days or []


def _entry_to_row(board: str, entry: dict, runnable: bool, now: datetime,
                  records: list[dict]) -> Row:
    window = entry.get("window") or ["", ""]
    row = Row(
        board=board,
        enabled=bool(entry.get("enabled")),
        mode="every" if entry.get("every_minutes") else "at",
        at=", ".join(entry.get("at") or []),
        every_minutes=str(entry.get("every_minutes") or ""),
        window_from=window[0] if len(window) == 2 else "",
        window_to=window[1] if len(window) == 2 else "",
        days=list(entry.get("days") or []),
        runnable=runnable,
        summary=schedule.describe(entry),
    )
    upcoming = schedule.next_slot(entry, now)
    row.next_due = upcoming.strftime("%a %H:%M") if upcoming else ""
    previous = schedule.last_run(board, records)
    row.last_run = previous.strftime("%a %H:%M") if previous else ""
    # Scheduled, allowed to run, and still waiting: either the timer is not loaded or scans are
    # failing. Either way the page must say so rather than look serene.
    row.overdue = bool(row.enabled and runnable and board in schedule.due(now, {board: entry}, records))
    return row


def rows(boards: list[str], config: dict, now: datetime | None = None) -> list[Row]:
    now = now or datetime.now()
    stored = schedule.load()
    records = run_record.load()
    configured = config.get("boards") or {}
    return [
        _entry_to_row(board, stored.get(board) or {}, bool((configured.get(board) or {}).get("enabled")),
                      now, records)
        for board in boards
    ]


def apply_enabled(boards: list[str], form, config: dict, path: Path | None = None) -> list[str]:
    """Switch boards on and off in config.yml to match the form. Returns what changed.

    This is the only thing here that writes outside `outputs/`. `config.yml` is the project's
    single input and full of hand-written comments, so `board_config.set_board_enabled` edits
    the one line and verifies the result before keeping it.
    """
    configured = config.get("boards") or {}
    changed = []
    for board in boards:
        if board not in configured:
            continue                    # a board with no config block is not ours to invent
        wanted = form.get(f"{board}.runnable") == "on"
        if bool((configured[board] or {}).get("enabled")) == wanted:
            continue
        if board_config.set_board_enabled(board, wanted, path or CONFIG):
            changed.append(f"{board} {'enabled' if wanted else 'disabled'}")
    return changed


def form_to_entries(boards: list[str], form) -> tuple[dict, dict[str, list[str]]]:
    """Turn a submitted form into schedule entries, plus whatever is wrong with them.

    Only entries that are switched on are validated: a board being off is not a claim that its
    times make sense, and refusing to save because a disabled board has a stale field would be
    infuriating.
    """
    entries: dict = {}
    problems: dict[str, list[str]] = {}

    for board in boards:
        enabled = form.get(f"{board}.enabled") == "on"
        mode = form.get(f"{board}.mode") or "at"
        entry: dict = {"enabled": enabled}

        if mode == "every":
            minutes = (form.get(f"{board}.every_minutes") or "").strip()
            entry["every_minutes"] = int(minutes) if minutes.isdigit() else minutes or None
            start = (form.get(f"{board}.window_from") or "").strip()
            end = (form.get(f"{board}.window_to") or "").strip()
            if start and end:
                entry["window"] = [start, end]
        else:
            times = [t.strip() for t in (form.get(f"{board}.at") or "").split(",") if t.strip()]
            entry["at"] = times

        days = [int(d) for d in form.getlist(f"{board}.days") if str(d).isdigit()]
        if days:
            entry["days"] = sorted(days)

        entry = {k: v for k, v in entry.items() if v not in (None, "", [])} | {"enabled": enabled}
        if enabled:
            found = schedule.validate(entry)
            if found:
                problems[board] = found
        entries[board] = entry

    return entries, problems


def save(entries: dict) -> None:
    schedule.save(entries)


def timer_state() -> dict:
    """Whether anything is actually going to consult this timetable.

    launchd is the answer on a Mac. In a container it is the compose `scheduler` service, which
    this process cannot see, so that deployment says so through JOB_CRAWLER_TIMER — the page's
    warning exists to catch a timetable nothing reads, and there it would be crying wolf.
    """
    external = os.environ.get("JOB_CRAWLER_TIMER", "").strip()
    if external:
        return {"loaded": True, "plist": "", "label": external, "install_command": ""}
    return {
        "loaded": install_timer.is_loaded(),
        "plist": str(install_timer.PLIST),
        "label": install_timer.LABEL,
        "install_command": f"{ROOT}/.venv/bin/python reed_crawler/install_timer.py --install",
    }
