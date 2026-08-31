"""The runner's HTTP surface, and the dashboard asking it instead of spawning."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reed_crawler"))

import run_record
from dashboard import runner_client
from dashboard.app import app as dashboard_app
from runner import pool, scans
from runner.app import app as runner_app

runner = TestClient(runner_app)
dashboard = TestClient(dashboard_app)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(run_record, "STATE", tmp_path)
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "runs.json")
    monkeypatch.setattr(scans, "LOG_DIR", tmp_path / "logs")
    monkeypatch.delenv("RUNNER_URL", raising=False)
    return tmp_path


def running(state, board):
    (state / "runs.json").write_text(json.dumps([
        {"id": f"r-{board}", "board": board, "status": scans.RUNNING, "started": "2026-08-27T09:00:00"}
    ]), encoding="utf-8")


# ---- the runner ----

def test_an_unknown_board_is_refused_rather_than_spawned():
    assert runner.post("/scan/nosuchboard").status_code == 404


def test_a_board_already_being_scanned_is_refused(isolated_state):
    running(isolated_state, "reed")
    response = runner.post("/scan/reed")
    assert response.status_code == 409
    assert "already being scanned" in response.json()["detail"]


def test_nothing_due_is_an_ordinary_answer_not_an_error(monkeypatch):
    """The timer calls this every ten minutes and mostly has nothing to do."""
    monkeypatch.setattr(scans.scan_all, "due_boards", lambda config: [])
    response = runner.post("/scan-due")
    assert response.status_code == 200
    assert response.json() == {"boards": []}


def test_scan_due_asks_the_same_question_the_timer_asks(monkeypatch):
    asked = {}
    monkeypatch.setattr(scans.scan_all, "due_boards", lambda config: asked.setdefault("boards", ["reed"]))
    monkeypatch.setattr("runner.app._start_pool", lambda boards: None)
    assert runner.post("/scan-due").json() == {"boards": ["reed"]}
    assert asked["boards"] == ["reed"]


def test_health_names_what_can_be_scanned():
    assert runner.get("/health").json()["boards"] == list(scans.COMMANDS)


# ---- the dashboard as a client ----

def test_the_dashboard_asks_the_runner_rather_than_spawning(monkeypatch):
    monkeypatch.setenv("RUNNER_URL", "http://runner:8081")
    asked = []

    async def fake_post(path):
        asked.append(path)
        return {"id": "2026-08-27_090000-reed", "board": "reed"}

    monkeypatch.setattr(runner_client, "post", fake_post)
    monkeypatch.setattr(scans.scans, "start", _must_not_spawn)

    response = dashboard.post("/api/scan/reed")

    assert response.status_code == 200
    assert response.json()["run_id"] == "2026-08-27_090000-reed"
    assert asked == ["/scan/reed?trigger=dashboard"]


async def _must_not_spawn(*args, **kwargs):
    raise AssertionError("the dashboard spawned a scan while a runner was configured")


def test_the_runners_refusal_reaches_the_browser_as_its_own_answer(monkeypatch):
    """A 409 means the board is busy. Flattening it to 502 would read as the runner being down."""
    monkeypatch.setenv("RUNNER_URL", "http://runner:8081")

    async def refuse(path):
        raise runner_client.RunnerError("reed is already being scanned", status=409)

    monkeypatch.setattr(runner_client, "post", refuse)

    assert dashboard.post("/api/scan/reed").status_code == 409


def test_without_a_runner_the_dashboard_starts_the_scan_itself(monkeypatch):
    """Running natively there is no second process, and the workflow must still work."""
    started = []

    async def fake_start(board, trigger="dashboard"):
        started.append(board)
        return scans.ScanRun(id="r1", board=board, started="2026-08-27T09:00:00")

    monkeypatch.setattr(scans.scans, "start", fake_start)

    response = dashboard.post("/api/scan/reed")

    assert response.status_code == 200
    assert response.json()["run_id"] == "r1"
    assert started == ["reed"]


def test_an_unreachable_runner_is_reported_not_silently_ignored(monkeypatch):
    monkeypatch.setenv("RUNNER_URL", "http://127.0.0.1:9")     # discard port: nothing listens
    response = dashboard.post("/api/scan/reed")
    assert response.status_code == 502
    assert "not answering" in response.json()["detail"]
