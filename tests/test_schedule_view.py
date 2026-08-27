from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config
import run_record
import scan_all
import schedule
from dashboard import ingest_view, schedule_view
from runner import pool, scans
from dashboard.app import app

client = TestClient(app)


CONFIG = """\
boards:
  reed:
    # A comment that must survive being edited around.
    enabled: true
    proximity: 50
    full_jd:
      enabled: true
  email:
    enabled: false
    messages_per_label: 25
"""


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """No test may touch the real schedule, history, config.yml or downstream workspace.

    config.yml especially: this page can now switch boards on and off, and a test run that
    quietly disabled a board would be found out days later, in the shape of missing jobs.
    """
    config_path = tmp_path / "config.yml"
    config_path.write_text(CONFIG, encoding="utf-8")

    monkeypatch.setattr(schedule, "STATE", tmp_path)
    monkeypatch.setattr(schedule, "SCHEDULE_FILE", tmp_path / "schedule.json")
    monkeypatch.setattr(run_record, "STATE", tmp_path)
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "runs.json")
    monkeypatch.setattr(schedule_view, "CONFIG", config_path)
    monkeypatch.setattr(pool, "load_config",
                        lambda: board_config.load_config(config_path))
    monkeypatch.setattr(schedule_view.install_timer, "is_loaded", lambda: True)
    return tmp_path


def config_says(state) -> dict:
    boards = board_config.load_config(state / "config.yml")["boards"]
    return {name: bool(entry.get("enabled")) for name, entry in boards.items()}


def saved(state) -> dict:
    return json.loads((state / "schedule.json").read_text())["boards"]


# ---- the page ----

def test_the_schedule_page_lists_every_scannable_board():
    page = client.get("/schedule")

    assert page.status_code == 200
    for board in ("reed", "totaljobs", "email"):
        assert board in page.text


def test_the_config_state_of_each_board_is_shown_as_a_box():
    page = client.get("/schedule").text

    assert 'name="reed.runnable" checked' in page       # enabled in config.yml
    assert 'name="email.runnable" checked' not in page  # disabled there


def test_a_container_names_its_own_timer_rather_than_warning_about_launchd(monkeypatch):
    """Off a Mac the timer is a separate service, so the launchd warning would be crying wolf."""
    monkeypatch.setenv("JOB_CRAWLER_TIMER", "the compose scheduler service")
    monkeypatch.setattr(schedule_view.install_timer, "is_loaded", lambda: False)

    state = schedule_view.timer_state()

    assert state["loaded"] is True
    assert state["label"] == "the compose scheduler service"


def test_the_page_says_plainly_when_no_timer_will_read_the_schedule(monkeypatch):
    monkeypatch.delenv("JOB_CRAWLER_TIMER", raising=False)
    monkeypatch.setattr(schedule_view.install_timer, "is_loaded", lambda: False)

    assert "Nothing is reading this timetable" in client.get("/schedule").text


# ---- saving ----

def test_a_timetable_is_saved_and_shown_back(isolated_state):
    response = client.post("/schedule", data={"reed.enabled": "on", "reed.mode": "at",
                                              "reed.at": "07:00, 18:30", "reed.days": ["0", "4"]},
                           follow_redirects=False)

    assert response.status_code == 303
    assert saved(isolated_state)["reed"] == {"enabled": True, "at": ["07:00", "18:30"], "days": [0, 4]}
    assert "at 07:00, 18:30 on Mon, Fri" in client.get("/schedule").text


def test_an_interval_with_a_window_is_saved(isolated_state):
    client.post("/schedule", data={"email.enabled": "on", "email.mode": "every",
                                   "email.every_minutes": "120",
                                   "email.window_from": "07:00", "email.window_to": "21:00"},
                follow_redirects=False)

    assert saved(isolated_state)["email"] == {"enabled": True, "every_minutes": 120,
                                              "window": ["07:00", "21:00"]}


def test_an_invalid_time_saves_nothing_and_says_why(isolated_state):
    response = client.post("/schedule", data={"reed.enabled": "on", "reed.mode": "at",
                                              "reed.at": "half seven"})

    assert response.status_code == 400
    assert "is not a time of day" in response.text
    assert not (isolated_state / "schedule.json").exists()


def test_a_switched_off_board_is_not_held_to_its_leftover_fields(isolated_state):
    # Turning a board off should always work, whatever is left in its inputs.
    response = client.post("/schedule", data={"reed.mode": "at", "reed.at": "nonsense"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert saved(isolated_state)["reed"]["enabled"] is False


def test_the_overview_warns_when_a_schedule_has_no_timer(monkeypatch, isolated_state):
    monkeypatch.delenv("JOB_CRAWLER_TIMER", raising=False)
    schedule.save({"reed": {"enabled": True, "at": ["07:00"]}})
    monkeypatch.setattr(schedule_view.install_timer, "is_loaded", lambda: False)

    assert "the timer is\n  not loaded" in client.get("/").text.replace("\r", "")


# ---- ingest ----

def workspace(tmp_path) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "pipeline.md").write_text("# Pipeline\n\n## Pending\n\n", encoding="utf-8")
    (tmp_path / "portals.yml").write_text(
        "title_filter:\n  positive: [Senior Software Engineer]\n  negative: []\n", encoding="utf-8")
    return tmp_path


def report_for(tmp_path, board: str, rows: list[dict]) -> Path:
    reports = tmp_path / "outputs" / board / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{board}_deduped_2026-08-19_100000.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def lead(**over) -> dict:
    return {"role_title": "Senior Software Engineer", "company": "Acme", "location": "Leeds",
            "posted": "2026-08-18", "url": "https://example.com/job/1", "job_id": "email-1",
            "source": "email", **over}


def test_the_ingest_page_previews_without_writing(tmp_path, monkeypatch):
    base = workspace(tmp_path / "career-ops")
    report = report_for(tmp_path, "email", [lead()])
    monkeypatch.setattr(ingest_view, "workspace", lambda: base)
    monkeypatch.setattr(ingest_view.ingest_jobspy, "latest_report",
                        lambda board: report if board == "email" else (_ for _ in ()).throw(SystemExit("no report")))
    before = (base / "data" / "pipeline.md").read_text(encoding="utf-8")

    page = client.get("/ingest")

    assert "Senior Software Engineer" in page.text     # the row
    assert "Send 1" in page.text                       # the button for its board
    assert (base / "data" / "pipeline.md").read_text(encoding="utf-8") == before


def test_ingesting_a_report_that_is_no_longer_the_newest_is_refused(tmp_path, monkeypatch):
    base = workspace(tmp_path / "career-ops")
    report = report_for(tmp_path, "email", [lead()])
    monkeypatch.setattr(ingest_view, "workspace", lambda: base)
    monkeypatch.setattr(ingest_view.ingest_jobspy, "latest_report", lambda board: report)

    response = client.post("/ingest/email", data={"report": "email_deduped_2026-08-01_090000.json"})

    assert response.status_code == 409
    assert "reload and look again" in response.text


def test_ingesting_runs_the_command_rather_than_reimplementing_it(tmp_path, monkeypatch):
    base = workspace(tmp_path / "career-ops")
    report = report_for(tmp_path, "email", [lead()])
    called = {}

    async def fake_run(board, expected):
        called["board"], called["expected"] = board, expected
        return 0, "Appended 1"

    monkeypatch.setattr(ingest_view, "workspace", lambda: base)
    monkeypatch.setattr(ingest_view.ingest_jobspy, "latest_report", lambda board: report)
    monkeypatch.setattr(ingest_view, "run", fake_run)

    response = client.post("/ingest/email", data={"report": report.name, "count": "1"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert called == {"board": "email", "expected": report.name}


def test_the_scheduler_has_no_route_into_the_workspace():
    # The ingest is a click. Nothing in the scan path may reach it.
    import scan_all

    for command in scan_all.COMMANDS.values():
        assert "ingest" not in " ".join(command)


# ---- switching boards on and off ----

def test_a_board_can_be_enabled_from_the_page(isolated_state):
    response = client.post("/schedule", data={"reed.runnable": "on", "email.runnable": "on",
                                              "email.enabled": "on", "email.mode": "every",
                                              "email.every_minutes": "120"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert config_says(isolated_state) == {"reed": True, "email": True}
    assert "email" in response.headers["location"] and "enabled" in response.headers["location"]


def test_a_board_can_be_disabled_from_the_page(isolated_state):
    client.post("/schedule", data={"email.runnable": "on"}, follow_redirects=False)   # on
    client.post("/schedule", data={}, follow_redirects=False)                          # both off

    assert config_says(isolated_state) == {"reed": False, "email": False}


def test_editing_the_config_keeps_everything_else_in_it(isolated_state):
    client.post("/schedule", data={"email.runnable": "on"}, follow_redirects=False)

    text = (isolated_state / "config.yml").read_text(encoding="utf-8")
    assert "# A comment that must survive being edited around." in text
    assert "proximity: 50" in text
    # A board's own `enabled` is four spaces in; its full_jd block has one of its own.
    assert "    full_jd:\n      enabled: true\n" in text


def test_a_board_with_no_config_block_is_not_invented(isolated_state):
    client.post("/schedule", data={"haystack.runnable": "on"}, follow_redirects=False)

    assert "haystack" not in (isolated_state / "config.yml").read_text(encoding="utf-8")


def test_an_invalid_timetable_leaves_the_config_alone_too(isolated_state):
    # Nothing was saved means nothing at all: not the schedule, and not config.yml either.
    response = client.post("/schedule", data={"email.runnable": "on", "email.enabled": "on",
                                              "email.mode": "at", "email.at": "half seven"})

    assert response.status_code == 400
    assert config_says(isolated_state)["email"] is False


# ---- sending everything downstream at once ----

def _two_boards(tmp_path, monkeypatch):
    base = workspace(tmp_path / "career-ops")
    reports = {
        "email": report_for(tmp_path, "email", [lead(url="https://example.com/job/e1", job_id="email-1")]),
        "reed": report_for(tmp_path, "reed", [lead(url="https://example.com/job/r1", job_id="reed-1")]),
    }
    monkeypatch.setattr(ingest_view, "workspace", lambda: base)
    monkeypatch.setattr(ingest_view.ingest_jobspy, "latest_report",
                        lambda board: reports.get(board) or (_ for _ in ()).throw(SystemExit("no report")))
    return base, reports


def test_send_all_appears_only_when_more_than_one_board_has_something(tmp_path, monkeypatch):
    _two_boards(tmp_path, monkeypatch)

    assert "Send all 2 to the pipeline" in client.get("/ingest").text


def test_send_all_ingests_every_previewed_board(tmp_path, monkeypatch):
    _, reports = _two_boards(tmp_path, monkeypatch)
    ran = []

    async def fake_run(board, expected):
        ran.append((board, expected))
        return 0, "Appended 1"

    monkeypatch.setattr(ingest_view, "run", fake_run)

    response = client.post("/ingest-all",
                           data={"report.email": reports["email"].name,
                                 "report.reed": reports["reed"].name, "count": "2"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert sorted(ran) == [("email", reports["email"].name), ("reed", reports["reed"].name)]


def test_send_all_skips_a_board_whose_report_moved_and_still_sends_the_rest(tmp_path, monkeypatch):
    _, reports = _two_boards(tmp_path, monkeypatch)
    sent = []

    async def fake_run(board, expected):
        if board == "reed":
            raise ValueError("newer than the one you were shown")
        sent.append(board)
        return 0, "Appended 1"

    monkeypatch.setattr(ingest_view, "run", fake_run)

    response = client.post("/ingest-all",
                           data={"report.email": reports["email"].name, "report.reed": "stale.json"},
                           follow_redirects=False)

    assert sent == ["email"]
    assert "skipped=reed" in response.headers["location"]


def test_send_all_with_nothing_pending_is_harmless(tmp_path, monkeypatch):
    _two_boards(tmp_path, monkeypatch)

    response = client.post("/ingest-all", data={}, follow_redirects=False)

    assert response.status_code == 303


# ---- reading the table ----

def _mixed_rows(tmp_path, monkeypatch):
    base = workspace(tmp_path / "career-ops")
    reports = {
        "email": report_for(tmp_path, "email", [
            lead(url="https://example.com/e1", job_id="e1", company="Monzo", location="London"),
            lead(url="https://example.com/e2", job_id="e2", company="Acme", location="Leeds"),
        ]),
        "reed": report_for(tmp_path, "reed", [
            lead(url="https://example.com/r1", job_id="r1", company="Wise", location="London"),
        ]),
    }
    monkeypatch.setattr(ingest_view, "workspace", lambda: base)
    monkeypatch.setattr(ingest_view.ingest_jobspy, "latest_report",
                        lambda board: reports.get(board) or (_ for _ in ()).throw(SystemExit("no report")))
    return base


def rows_shown(page: str) -> int:
    # One <tr> per candidate in the second table; the summary table has one row per board.
    return page.count('target="_blank"')


def test_every_candidate_is_a_row_with_its_own_fields(tmp_path, monkeypatch):
    _mixed_rows(tmp_path, monkeypatch)

    page = client.get("/ingest").text

    assert rows_shown(page) == 3
    assert "Monzo" in page and "Wise" in page
    assert "3 of 3 shown" in page


def test_the_table_narrows_by_board(tmp_path, monkeypatch):
    _mixed_rows(tmp_path, monkeypatch)

    page = client.get("/ingest?board=reed").text

    assert rows_shown(page) == 1
    assert "Wise" in page and "Monzo" not in page


def test_the_table_narrows_by_text_across_its_columns(tmp_path, monkeypatch):
    _mixed_rows(tmp_path, monkeypatch)

    assert rows_shown(client.get("/ingest?q=london").text) == 2       # location
    assert rows_shown(client.get("/ingest?q=acme").text) == 1         # company
    assert rows_shown(client.get("/ingest?q=london+wise").text) == 1  # both terms must match
    assert rows_shown(client.get("/ingest?q=nothing").text) == 0


def test_filtering_does_not_change_what_a_button_sends(tmp_path, monkeypatch):
    # The filter is a way of reading the list, not of choosing from it: ingest appends a whole
    # report, and the buttons must keep saying so.
    _mixed_rows(tmp_path, monkeypatch)

    page = client.get("/ingest?board=reed").text

    assert "Send all 3 to the pipeline" in page
    assert "Send 2" in page          # email's own button still offers both of its rows


# ---- triggering by hand ----

def test_each_runnable_board_offers_a_run_button(isolated_state):
    page = client.get("/schedule").text

    assert 'form="run-reed"' in page                       # enabled in the test config
    assert '<form id="run-reed" method="post" action="/scan/reed">' in page
    assert 'form="run-email"' not in page                  # disabled there, so nothing to run


def test_run_due_starts_exactly_what_the_timer_would(isolated_state, monkeypatch):
    started = []
    monkeypatch.setattr(schedule_view, "CONFIG", isolated_state / "config.yml")
    monkeypatch.setattr(scan_all, "due_boards", lambda config: ["reed"])
    monkeypatch.setattr(scans.scans, "board_is_running", lambda board: False)

    async def fake_start(board, trigger="pool"):
        started.append(board)
        return scans.ScanRun(id=f"x-{board}", board=board)

    monkeypatch.setattr(scans.scans, "start_and_wait", fake_start)

    response = client.post("/schedule/run-due", follow_redirects=False)

    assert response.status_code == 303
    assert "started=reed" in response.headers["location"]
    for _ in range(50):                       # the pool starts them in a background task
        if started:
            break
        time.sleep(0.02)
    assert started == ["reed"]


def test_run_due_with_nothing_due_says_so_rather_than_scanning(monkeypatch):
    monkeypatch.setattr(scan_all, "due_boards", lambda config: [])

    response = client.post("/schedule/run-due", follow_redirects=False)

    assert "nothing" in response.headers["location"]


def test_run_due_skips_a_board_that_is_already_scanning(monkeypatch):
    monkeypatch.setattr(scan_all, "due_boards", lambda config: ["reed"])
    monkeypatch.setattr(scans.scans, "board_is_running", lambda board: True)

    response = client.post("/schedule/run-due", follow_redirects=False)

    assert "nothing" in response.headers["location"]
