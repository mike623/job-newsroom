"""When each board should be scanned.

`config.yml` says which boards may run; this says when. Keeping them apart is what lets the
dashboard own the timetable without being able to switch a board on: a schedule entry for a
board the config has disabled simply never fires.

The operating system's timer stays dumb. It asks "is anything due?" every few minutes and this
answers; a schedule change is a JSON edit rather than a change to a launchd agent, so the agent
is installed once and never touched again.

"Has this board run since its slot?" is answered from `run_record`, the same history the runs
page shows, so a scan somebody started by hand also satisfies the schedule — the point is that
a board was scanned, not that this particular timer scanned it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import run_record

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "outputs" / "state"
SCHEDULE_FILE = STATE / "schedule.json"

# Weekday numbers as datetime.weekday() gives them: Monday is 0.
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

MIN_INTERVAL_MINUTES = 5


def load() -> dict:
    """Every board's timetable, or an empty one.

    A missing or unreadable file means "nothing is scheduled", never an error: the crawler must
    still run by hand on a machine where this was never set up.
    """
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    boards = data.get("boards") if isinstance(data, dict) else None
    return boards if isinstance(boards, dict) else {}


def save(boards: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps({"boards": boards}, indent=2), encoding="utf-8")


def parse_time(value: str) -> tuple[int, int] | None:
    """"07:30" as (hour, minute), or None if that is not a time of day."""
    parts = str(value or "").strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    return (hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None


def validate(entry: dict) -> list[str]:
    """What is wrong with one board's entry, in words a person can act on.

    Validation lives here rather than in the dashboard so the file cannot be made invalid by
    editing it directly either.
    """
    problems = []
    times = entry.get("at") or []
    every = entry.get("every_minutes")

    if bool(times) == bool(every):
        problems.append("choose either fixed times or an interval, not both and not neither")

    for value in times:
        if parse_time(value) is None:
            problems.append(f"{value!r} is not a time of day (use HH:MM)")

    if every is not None:
        try:
            minutes = int(every)
        except (TypeError, ValueError):
            problems.append(f"{every!r} is not a number of minutes")
        else:
            if minutes < MIN_INTERVAL_MINUTES:
                problems.append(f"an interval under {MIN_INTERVAL_MINUTES} minutes is too often")

    window = entry.get("window") or []
    if window:
        if len(window) != 2 or any(parse_time(w) is None for w in window):
            problems.append("a window needs a start and an end time (HH:MM)")

    for day in entry.get("days") or []:
        if not isinstance(day, int) or not 0 <= day <= 6:
            problems.append(f"{day!r} is not a weekday (0 is Monday, 6 is Sunday)")

    return problems


def _at_slots(entry: dict, day: datetime) -> list[datetime]:
    """The fixed times this entry names on a given date."""
    slots = []
    for value in entry.get("at") or []:
        parsed = parse_time(value)
        if parsed:
            slots.append(day.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0))
    return sorted(slots)


def _in_window(entry: dict, moment: datetime) -> bool:
    window = entry.get("window") or []
    if len(window) != 2:
        return True
    start, end = parse_time(window[0]), parse_time(window[1])
    if not start or not end:
        return True
    minutes = moment.hour * 60 + moment.minute
    first, last = start[0] * 60 + start[1], end[0] * 60 + end[1]
    # A window that ends before it starts runs through midnight.
    return first <= minutes <= last if first <= last else (minutes >= first or minutes <= last)


def _runs_today(entry: dict, day: datetime) -> bool:
    days = entry.get("days")
    return not days or day.weekday() in days


def last_slot(entry: dict, now: datetime) -> datetime | None:
    """The most recent moment this board was supposed to be scanned, at or before `now`.

    Only the latest one: a machine that slept through three days of 07:00 slots scans once when
    it wakes, rather than replaying every morning it missed.
    """
    if not entry.get("enabled"):
        return None

    if entry.get("every_minutes"):
        if not _runs_today(entry, now) or not _in_window(entry, now):
            return None
        try:
            minutes = max(MIN_INTERVAL_MINUTES, int(entry["every_minutes"]))
        except (TypeError, ValueError):
            return None
        # The slot is "one interval ago": inside its window, an interval board is due whenever
        # that long has passed since it last ran.
        return now - timedelta(minutes=minutes)

    # Today's timetable only. A slot belongs to its own day, so a machine that was asleep at
    # 07:00 and wakes at 09:00 still catches up, while one that stays off until tomorrow has
    # simply missed that day rather than starting a scan the moment it is turned on.
    if not _runs_today(entry, now):
        return None
    passed = [slot for slot in _at_slots(entry, now) if slot <= now]
    return passed[-1] if passed else None


def next_slot(entry: dict, now: datetime) -> datetime | None:
    """The next moment this board is due, for display."""
    if not entry.get("enabled"):
        return None

    if entry.get("every_minutes"):
        try:
            minutes = max(MIN_INTERVAL_MINUTES, int(entry["every_minutes"]))
        except (TypeError, ValueError):
            return None
        return now + timedelta(minutes=minutes)

    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        if not _runs_today(entry, day):
            continue
        for slot in _at_slots(entry, day):
            if slot > now:
                return slot
    return None


def last_run(board: str, records: list[dict] | None = None) -> datetime | None:
    """When this board was last scanned, by anything at all.

    Busy runs do not count: a scan that found the board locked never asked the board anything.

    A failed or interrupted run does count, deliberately. The alternative is retrying a broken
    board on every tick, which for a board that is failing because it has started blocking us
    is the worst possible response. A failure waits for the next slot, and says so on /runs.
    """
    latest = None
    for record in (records if records is not None else run_record.load()):
        if record.get("board") != board or record.get("status") == run_record.BUSY:
            continue
        try:
            started = datetime.fromisoformat(record.get("started") or "")
        except ValueError:
            continue
        if latest is None or started > latest:
            latest = started
    return latest


def due(now: datetime | None = None, boards: dict | None = None,
        records: list[dict] | None = None) -> list[str]:
    """Boards whose slot has passed and which have not been scanned since it."""
    now = now or datetime.now()
    boards = load() if boards is None else boards
    records = run_record.load() if records is None else records

    ready = []
    for board, entry in boards.items():
        if validate(entry):
            continue                            # an entry that cannot be read is not a schedule
        slot = last_slot(entry, now)
        if slot is None:
            continue
        previous = last_run(board, records)
        if previous is None or previous < slot:
            ready.append(board)
    return ready


def describe(entry: dict) -> str:
    """The timetable in one line, for the dashboard and the log."""
    if not entry.get("enabled"):
        return "off"
    days = entry.get("days") or []
    on = f" on {', '.join(DAY_NAMES[d] for d in sorted(days))}" if days else ""
    if entry.get("every_minutes"):
        window = entry.get("window") or []
        between = f" between {window[0]} and {window[1]}" if len(window) == 2 else ""
        return f"every {entry['every_minutes']} min{between}{on}"
    times = ", ".join(entry.get("at") or [])
    return f"at {times}{on}" if times else "off"
