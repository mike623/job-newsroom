from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard import aggregate
from dashboard.app import app


def write_run(outputs: Path, board: str, stamp: str, rows: list[dict]) -> None:
    reports = outputs / board / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{board}_deduped_{stamp}.json").write_text(json.dumps(rows), encoding="utf-8")


def job(job_id: str, title="leeds dev", location="leeds", **extra) -> dict:
    return {"job_id": job_id, "search_title": title, "search_location": location,
            "role_title": f"Role {job_id}", **extra}


def test_a_board_that_was_never_scanned_reports_nothing_rather_than_erroring(tmp_path):
    summary = aggregate.summarise_board("reed", tmp_path)

    assert summary.scanned is False
    assert (summary.known, summary.runs) == (0, 0)
    assert summary.last_run_display == "never"


def test_first_and_last_seen_and_the_sighting_count(tmp_path):
    for stamp in ["2026-08-01_090000", "2026-08-02_090000", "2026-08-03_090000"]:
        write_run(tmp_path, "reed", stamp, [job("1")])

    (only,) = aggregate.jobs_for_board("reed", tmp_path)

    assert only.first_seen == "2026-08-01_090000"
    assert only.last_seen == "2026-08-03_090000"
    assert only.times_seen == 3


def test_a_later_blank_never_erases_a_value_an_earlier_run_captured(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1", company="Acme", salary_max=70000)])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("1", company="", salary_max=None)])

    (only,) = aggregate.jobs_for_board("reed", tmp_path)

    assert only.company == "Acme"
    assert only.pay == 70000


def test_new_counts_only_jobs_first_seen_in_the_latest_run(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("old")])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("old"), job("fresh")])

    assert aggregate.summarise_board("reed", tmp_path).new == 1


def test_unreadable_report_files_are_skipped_rather_than_crashing(tmp_path):
    reports = tmp_path / "reed" / "reports"
    reports.mkdir(parents=True)
    (reports / "reed_deduped_2026-08-01_090000.json").write_text("{ not json", encoding="utf-8")

    assert aggregate.summarise_board("reed", tmp_path).known == 0


def test_rows_without_a_job_id_are_ignored(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1"), {"job_id": "", "role_title": "junk"}])

    assert aggregate.summarise_board("reed", tmp_path).known == 1


@pytest.fixture
def client():
    return TestClient(app)


def test_the_overview_reports_every_board(client):
    response = client.get("/api/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body["totals"]) == {"known", "new", "runs"}
    assert [s["board"] for s in body["summaries"]] == aggregate.BOARDS


def test_no_api_docs_are_exposed(client):
    # Nothing here is a public API; the schema endpoints are noise on a single-user tool.
    # They are not merely unrouted — the client's catch-all would answer with its index.html,
    # so what matters is that no schema comes back.
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert "application/json" not in client.get(path).headers.get("content-type", "")


def test_an_unknown_api_path_is_a_404_rather_than_the_client(client):
    # A failed fetch that reads as HTML is the kind of bug that takes an afternoon.
    assert client.get("/api/nothing-here").status_code == 404
