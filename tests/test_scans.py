from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reed_crawler"))

import run_record
from runner import scans
from dashboard.app import app


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test off the real outputs/state.

    The history itself lives in run_record now: every scan records itself there, whatever
    started it. scans only owns the captured logs.
    """
    monkeypatch.setattr(run_record, "STATE", tmp_path)
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "runs.json")
    monkeypatch.setattr(scans, "LOG_DIR", tmp_path / "logs")
    # These tests model the process that starts the scans, which is the only one allowed to
    # judge whether their pids are still alive.
    monkeypatch.setattr(scans, "SPAWNS_SCANS", True)
    return tmp_path


def record(**overrides):
    base = {"id": "r1", "board": "reed", "status": scans.RUNNING, "started": "2026-08-14T09:00:00"}
    return {**base, **overrides}


def write_records(state, records):
    (state / "runs.json").write_text(json.dumps(records), encoding="utf-8")


# ---- persistence ----

def test_history_is_newest_first_and_survives_a_restart(isolated_state):
    write_records(isolated_state, [
        record(id="old", started="2026-08-14T09:00:00"),
        record(id="new", started="2026-08-15T09:00:00"),
    ])

    assert [r.id for r in scans.history()] == ["new", "old"]


def test_history_tolerates_unknown_keys_from_an_older_format(isolated_state):
    write_records(isolated_state, [record(id="r1", something_removed="x")])

    assert scans.history()[0].id == "r1"


def test_a_missing_or_corrupt_runs_file_yields_no_history(isolated_state):
    assert scans.history() == []

    (isolated_state / "runs.json").write_text("{ not json", encoding="utf-8")
    assert scans.history() == []


# ---- reconciliation ----

def test_a_run_whose_process_is_gone_is_marked_interrupted(isolated_state):
    write_records(isolated_state, [record(status=scans.RUNNING, pid=2 ** 30)])

    scans.reconcile()

    assert scans.history()[0].status == scans.INTERRUPTED


def test_a_run_whose_process_is_alive_is_left_running(isolated_state):
    import os
    write_records(isolated_state, [record(status=scans.RUNNING, pid=os.getpid())])

    scans.reconcile()

    assert scans.history()[0].status == scans.RUNNING


def test_a_process_that_did_not_spawn_the_scan_never_judges_its_pid(isolated_state, monkeypatch):
    """The dashboard reads these records from its own container.

    A pid means nothing outside the namespace that made it, so signalling a runner's pid from the
    dashboard reports a perfectly live scan as gone. Every page render calls history(), so left
    unguarded this marks running scans interrupted and then lets a second one start.
    """
    monkeypatch.setattr(scans, "SPAWNS_SCANS", False)
    write_records(isolated_state, [record(status=scans.RUNNING, pid=2 ** 30)])

    scans.reconcile()

    assert scans.history()[0].status == scans.RUNNING
    assert scans.scans.board_is_running("reed") is True


def test_finished_runs_are_untouched_by_reconciliation(isolated_state):
    write_records(isolated_state, [record(status=scans.DONE, exit_code=0, pid=2 ** 30)])

    scans.reconcile()

    assert scans.history()[0].status == scans.DONE


# ---- running a scan ----

def test_a_scan_records_its_outcome_and_captures_output(isolated_state, monkeypatch):
    monkeypatch.setitem(scans.COMMANDS, "reed", ["-c", "print('hello from the scan')"])

    async def go():
        run = await scans.scans.start("reed")
        for _ in range(200):
            current = scans.get(run.id)
            if current and current.status != scans.RUNNING:
                return current
            await asyncio.sleep(0.05)
        pytest.fail("the scan never finished")

    finished = asyncio.run(go())

    assert finished.status == scans.DONE
    assert finished.exit_code == 0
    assert "hello from the scan" in finished.log_path.read_text()


def test_a_failing_scan_is_recorded_as_failed(isolated_state, monkeypatch):
    monkeypatch.setitem(scans.COMMANDS, "reed", ["-c", "import sys; sys.exit(2)"])

    async def go():
        run = await scans.scans.start("reed")
        for _ in range(200):
            current = scans.get(run.id)
            if current and current.status != scans.RUNNING:
                return current
            await asyncio.sleep(0.05)
        pytest.fail("the scan never finished")

    assert asyncio.run(go()).status == scans.FAILED


def test_a_scan_blocked_by_the_board_lock_is_recorded_as_busy_not_failed(isolated_state, monkeypatch):
    # Exit 75 is what the scan entrypoints use when another process holds the board.
    monkeypatch.setitem(scans.COMMANDS, "reed", ["-c", f"import sys; sys.exit({scans.BUSY_EXIT_CODE})"])

    async def go():
        run = await scans.scans.start("reed")
        for _ in range(200):
            current = scans.get(run.id)
            if current and current.status != scans.RUNNING:
                return current
            await asyncio.sleep(0.05)
        pytest.fail("the scan never finished")

    assert asyncio.run(go()).status == scans.BUSY


def test_an_unknown_board_cannot_be_started(isolated_state):
    with pytest.raises(KeyError):
        asyncio.run(scans.scans.start("myspace"))


# ---- routes ----

@pytest.fixture
def client():
    return TestClient(app)


def test_only_scanning_is_exposed():
    # Enriching and exporting write outside this project; they stay at the terminal.
    assert set(scans.COMMANDS) == {"reed", "totaljobs", "talent", "indeed", "adzuna", "haystack",
                                   "linkedin", "email"}
    for argv in scans.COMMANDS.values():
        assert "enrich" not in argv and "export" not in argv and "run" not in argv


def test_starting_an_unknown_board_is_a_404(client):
    assert client.post("/api/scan/myspace").status_code == 404


def test_a_board_already_being_scanned_is_refused(client, isolated_state):
    write_records(isolated_state, [record(board="reed", status=scans.RUNNING)])

    assert client.post("/api/scan/reed").status_code == 409


def test_an_unknown_run_is_a_404(client):
    assert client.get("/api/scan/nope").status_code == 404


def test_the_stream_reports_an_unknown_run_rather_than_hanging(isolated_state):
    async def collect():
        return [chunk async for chunk in scans.scans.stream("nope")]

    assert "unknown run" in "".join(asyncio.run(collect()))


def test_a_killed_scan_stops_looking_alive_without_a_restart(isolated_state):
    # A scheduled scan the machine killed cannot record its own ending. Left as "running" it
    # would block that board's button for ever.
    write_records(isolated_state, [record(id="r1", board="email", status=scans.RUNNING, pid=999_999)])

    assert scans.history()[0].status == scans.INTERRUPTED
    assert scans.scans.board_is_running("email") is False
