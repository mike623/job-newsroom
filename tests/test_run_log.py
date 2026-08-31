from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dashboard import aggregate
from dashboard.app import app

from test_dashboard import job, write_run


@pytest.fixture
def client():
    return TestClient(app)


def test_runs_are_listed_newest_first_across_boards(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1")])
    write_run(tmp_path, "totaljobs", "2026-08-03_090000", [job("2")])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("3")])

    found = aggregate.runs(outputs=tmp_path, recorded=[])

    assert [r.stamp for r in found] == ["2026-08-03_090000", "2026-08-02_090000", "2026-08-01_090000"]
    assert found[0].board == "totaljobs"


def test_a_run_records_how_many_searches_it_covered(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [
        job("1", location="leeds"), job("2", location="leeds"), job("3", location="manchester"),
    ])

    (run,) = aggregate.runs(outputs=tmp_path, recorded=[])

    assert run.jobs == 3
    assert run.searches == 2


def test_a_run_that_found_nothing_is_flagged(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [])

    (run,) = aggregate.runs(outputs=tmp_path, recorded=[])

    assert run.jobs == 0
    assert run.healthy is False


def test_runs_can_be_filtered_to_one_board(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1")])
    write_run(tmp_path, "talent", "2026-08-01_090000", [job("2")])

    assert {r.board for r in aggregate.runs("talent", tmp_path, recorded=[])} == {"talent"}


def test_the_display_time_is_readable(tmp_path):
    write_run(tmp_path, "reed", "2026-08-14_143005", [job("1")])

    assert aggregate.runs(outputs=tmp_path, recorded=[])[0].display_time == "2026-08-14 14:30"


# ---- paging ----

def test_paging_splits_the_list_and_reports_the_page_count():
    items = list(range(25))

    first, page, pages = aggregate.page_of(items, 1, 10)
    assert (first, page, pages) == ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 1, 3)

    last, page, pages = aggregate.page_of(items, 3, 10)
    assert (last, page, pages) == ([20, 21, 22, 23, 24], 3, 3)


def test_a_page_beyond_the_end_is_clamped_rather_than_returning_nothing():
    items = list(range(5))

    shown, page, pages = aggregate.page_of(items, 99, 10)

    assert shown == items
    assert (page, pages) == (1, 1)


def test_a_page_below_one_is_clamped():
    shown, page, _ = aggregate.page_of(list(range(5)), 0, 2)

    assert page == 1
    assert shown == [0, 1]


def test_an_empty_list_still_reports_one_page():
    shown, page, pages = aggregate.page_of([], 1, 10)

    assert (shown, page, pages) == ([], 1, 1)


# ---- route ----

def test_the_run_log_renders_and_pages(client):
    assert client.get("/api/runs").status_code == 200

    small = client.get("/api/runs?per_page=5")
    assert small.status_code == 200
    assert small.json()["per_page"] == 5 and small.json()["total"] > 5


def test_no_page_loads_the_whole_history(client):
    # The point of paging: the history is already thousands of rows and grows daily.
    body = client.get("/api/runs?per_page=5").json()

    assert len(body["runs"]) == 5 and body["total"] > 5


def test_an_absurd_per_page_is_capped(client):
    assert client.get("/api/runs?per_page=100000").status_code == 200
