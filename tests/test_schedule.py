from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import run_record
import schedule


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "STATE", tmp_path)
    monkeypatch.setattr(schedule, "SCHEDULE_FILE", tmp_path / "schedule.json")
    monkeypatch.setattr(run_record, "STATE", tmp_path)
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "runs.json")
    return tmp_path


def ran(board: str, started: str, status: str = run_record.DONE) -> dict:
    return {"id": f"{started}-{board}", "board": board, "started": started, "status": status}


MORNING = {"enabled": True, "at": ["07:00"]}
HOURLY = {"enabled": True, "every_minutes": 60}


# ---- reading the file ----

def test_no_schedule_file_means_nothing_is_scheduled():
    assert schedule.load() == {}
    assert schedule.due(datetime(2026, 8, 19, 9, 0)) == []


def test_a_corrupt_schedule_does_not_stop_the_crawler(isolated_state):
    (isolated_state / "schedule.json").write_text("{ not json", encoding="utf-8")
    assert schedule.load() == {}


def test_saving_and_loading_round_trips(isolated_state):
    schedule.save({"reed": MORNING})
    assert schedule.load() == {"reed": MORNING}
    assert json.loads((isolated_state / "schedule.json").read_text())["boards"]["reed"]["at"] == ["07:00"]


# ---- due-ness ----

def test_a_fixed_time_is_due_once_its_moment_has_passed():
    boards = {"reed": MORNING}
    assert schedule.due(datetime(2026, 8, 19, 6, 55), boards, []) == []
    assert schedule.due(datetime(2026, 8, 19, 7, 5), boards, []) == ["reed"]


def test_a_scan_since_the_slot_satisfies_it():
    records = [ran("reed", "2026-08-19T07:02:00")]
    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": MORNING}, records) == []


def test_a_scan_before_the_slot_does_not():
    records = [ran("reed", "2026-08-19T06:00:00")]
    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": MORNING}, records) == ["reed"]


def test_a_scan_started_by_anything_counts():
    # The schedule asks that the board be scanned, not that this timer scan it.
    records = [ran("reed", "2026-08-19T07:30:00")]
    records[0]["trigger"] = "dashboard"
    assert schedule.due(datetime(2026, 8, 19, 8, 0), {"reed": MORNING}, records) == []


def test_a_run_that_found_the_board_busy_does_not_count():
    records = [ran("reed", "2026-08-19T07:30:00", status=run_record.BUSY)]
    assert schedule.due(datetime(2026, 8, 19, 8, 0), {"reed": MORNING}, records) == ["reed"]


def test_three_missed_mornings_produce_one_scan_not_three():
    # A laptop asleep since Sunday wakes on Wednesday. It scans once and is then up to date.
    now = datetime(2026, 8, 19, 9, 0)
    assert schedule.due(now, {"reed": MORNING}, []) == ["reed"]
    caught_up = [ran("reed", "2026-08-19T09:01:00")]
    assert schedule.due(datetime(2026, 8, 19, 9, 5), {"reed": MORNING}, caught_up) == []


def test_an_interval_board_waits_out_its_interval():
    records = [ran("email", "2026-08-19T09:30:00")]
    assert schedule.due(datetime(2026, 8, 19, 10, 0), {"email": HOURLY}, records) == []
    assert schedule.due(datetime(2026, 8, 19, 10, 31), {"email": HOURLY}, records) == ["email"]


def test_an_interval_board_is_quiet_outside_its_window():
    nightly = {"enabled": True, "every_minutes": 60, "window": ["07:00", "21:00"]}
    assert schedule.due(datetime(2026, 8, 19, 23, 0), {"email": nightly}, []) == []
    assert schedule.due(datetime(2026, 8, 19, 12, 0), {"email": nightly}, []) == ["email"]


def test_a_window_may_run_through_midnight():
    overnight = {"enabled": True, "every_minutes": 60, "window": ["22:00", "04:00"]}
    assert schedule.due(datetime(2026, 8, 19, 23, 30), {"email": overnight}, []) == ["email"]
    assert schedule.due(datetime(2026, 8, 19, 12, 0), {"email": overnight}, []) == []


def test_weekdays_only_skips_the_weekend():
    weekdays = {"enabled": True, "at": ["07:00"], "days": [0, 1, 2, 3, 4]}
    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": weekdays}, []) == ["reed"]   # Wednesday
    assert schedule.due(datetime(2026, 8, 22, 9, 0), {"reed": weekdays}, []) == []         # Saturday


def test_a_disabled_entry_never_fires():
    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": {**MORNING, "enabled": False}}, []) == []


def test_an_invalid_entry_is_ignored_rather_than_guessed_at():
    broken = {"enabled": True, "at": ["七時"], "every_minutes": 30}
    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": broken}, []) == []


# ---- validation ----

def test_an_entry_must_choose_one_mode():
    assert schedule.validate({"enabled": True, "at": ["07:00"], "every_minutes": 60})
    assert schedule.validate({"enabled": True})
    assert schedule.validate({"enabled": True, "at": ["07:00"]}) == []
    assert schedule.validate({"enabled": True, "every_minutes": 60}) == []


def test_validation_names_what_is_wrong():
    problems = schedule.validate({"enabled": True, "at": ["25:00"]})
    assert any("time of day" in p for p in problems)
    assert any("too often" in p for p in schedule.validate({"enabled": True, "every_minutes": 1}))
    assert any("weekday" in p for p in schedule.validate({"enabled": True, "at": ["07:00"], "days": [9]}))
    assert any("window" in p for p in schedule.validate({"enabled": True, "every_minutes": 60,
                                                         "window": ["07:00"]}))


# ---- display ----

def test_the_next_slot_is_reported_for_display():
    assert schedule.next_slot(MORNING, datetime(2026, 8, 19, 9, 0)) == datetime(2026, 8, 20, 7, 0)
    weekend_off = {"enabled": True, "at": ["07:00"], "days": [0, 1, 2, 3, 4]}
    assert schedule.next_slot(weekend_off, datetime(2026, 8, 21, 9, 0)) == datetime(2026, 8, 24, 7, 0)
    assert schedule.next_slot({"enabled": False, "at": ["07:00"]}, datetime(2026, 8, 19, 9, 0)) is None


def test_a_timetable_reads_as_a_sentence():
    assert schedule.describe(MORNING) == "at 07:00"
    assert schedule.describe({"enabled": True, "at": ["07:00"], "days": [0, 4]}) == "at 07:00 on Mon, Fri"
    assert schedule.describe({"enabled": True, "every_minutes": 120, "window": ["07:00", "21:00"]}) \
        == "every 120 min between 07:00 and 21:00"
    assert schedule.describe({"enabled": False}) == "off"


def test_a_failed_scan_waits_for_its_next_slot_rather_than_retrying_at_once():
    # Retrying every tick is the worst answer to a board that has started blocking us.
    records = [ran("reed", "2026-08-19T07:01:00", status=run_record.FAILED)]

    assert schedule.due(datetime(2026, 8, 19, 9, 0), {"reed": MORNING}, records) == []
    assert schedule.due(datetime(2026, 8, 20, 9, 0), {"reed": MORNING}, records) == ["reed"]
