from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from dashboard import aggregate
from dashboard.app import app

from test_dashboard import job, write_run


@pytest.fixture
def client():
    return TestClient(app)


def make(board="reed", job_id="1", **fields):
    return aggregate.Job(board=board, job_id=job_id, first_seen="2026-08-01_090000",
                         last_seen="2026-08-02_090000", times_seen=2, fields=fields)


# ---- filtering ----

def test_board_filter():
    jobs = [make(board="reed", job_id="a"), make(board="talent", job_id="b")]

    assert [j.job_id for j in aggregate.select(jobs, board="talent")] == ["b"]


def test_a_pay_floor_excludes_adverts_that_state_no_figure():
    # An advert reading "Competitive" cannot be shown to clear £70k, so it is not offered as if
    # it might. Otherwise the filter would quietly become "or unknown".
    jobs = [make(job_id="high", salary_max=80000), make(job_id="low", salary_max=40000), make(job_id="none")]

    assert [j.job_id for j in aggregate.select(jobs, min_pay=70000)] == ["high"]


def test_search_matches_role_company_and_location():
    jobs = [
        make(job_id="r", role_title="Senior Rust Engineer"),
        make(job_id="c", role_title="Dev", company="Rustbelt Ltd"),
        make(job_id="l", role_title="Dev", location="Rusthall"),
        make(job_id="x", role_title="Dev", company="Acme", location="Leeds"),
    ]

    assert {j.job_id for j in aggregate.select(jobs, query="rust")} == {"r", "c", "l"}


def test_filters_combine():
    jobs = [
        make(board="reed", job_id="keep", salary_max=90000, role_title="Go dev"),
        make(board="reed", job_id="poor", salary_max=20000, role_title="Go dev"),
        make(board="talent", job_id="other", salary_max=90000, role_title="Go dev"),
    ]

    chosen = aggregate.select(jobs, board="reed", min_pay=70000, query="go")

    assert [j.job_id for j in chosen] == ["keep"]


# ---- ordering ----

def test_pay_sort_is_by_number_not_string_and_puts_unstated_last():
    jobs = [make(job_id="mid", salary_max=90000), make(job_id="none"), make(job_id="top", salary_max=120000)]

    assert [j.job_id for j in aggregate.select(jobs, sort="pay")] == ["top", "mid", "none"]


def test_name_sorts_run_a_to_z_while_the_rest_are_most_interesting_first():
    jobs = [make(job_id="b", company="Beta"), make(job_id="a", company="Alpha")]

    assert [j.job_id for j in aggregate.select(jobs, sort="company")] == ["a", "b"]


def test_jobs_the_ingest_would_drop_are_hidden_until_asked_for():
    """The list is for what is live and relevant; portals.yml has already rejected the rest."""
    keep = make(job_id="keep")
    drop = make(job_id="drop")
    drop.ingest_skip = "title: no match"

    assert [j.job_id for j in aggregate.select([keep, drop])] == ["keep"]
    assert {j.job_id for j in aggregate.select([keep, drop], skipped=True)} == {"keep", "drop"}


def test_an_unknown_sort_falls_back_rather_than_erroring():
    jobs = [make(job_id="a"), make(job_id="b")]

    assert len(aggregate.select(jobs, sort="nonsense")) == 2


# ---- detail ----

def test_the_detail_view_lists_every_run_that_saw_the_job(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1")])
    write_run(tmp_path, "reed", "2026-08-02_090000", [job("2")])
    write_run(tmp_path, "reed", "2026-08-03_090000", [job("1")])

    assert aggregate.sightings("reed", "1", tmp_path) == ["2026-08-03_090000", "2026-08-01_090000"]


def test_find_job_returns_nothing_for_an_unknown_id(tmp_path):
    write_run(tmp_path, "reed", "2026-08-01_090000", [job("1")])

    assert aggregate.find_job("reed", "1", tmp_path) is not None
    assert aggregate.find_job("reed", "missing", tmp_path) is None


# ---- routes ----

def test_the_job_list_answers_with_the_filters_it_offers(client):
    response = client.get("/api/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["shown"] == len(body["jobs"])
    assert body["sorts"] and body["boards"]


def test_an_unknown_job_or_board_is_a_404(client):
    assert client.get("/api/jobs/reed/definitely-not-a-job").status_code == 404
    assert client.get("/api/jobs/notaboard/1").status_code == 404


def test_csv_export_carries_the_structured_columns(client):
    response = client.get("/export.csv?")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="jobs.csv"'
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows, "expected at least one job in the export"
    for column in ("board", "job_id", "salary_min", "salary_max", "salary_period", "first_seen"):
        assert column in rows[0]


def test_csv_export_hides_skipped_jobs_like_the_page_does(client):
    # The page and the spreadsheet answer the same query string, or the download is a
    # different list from the one you were looking at.
    shown = client.get("/api/jobs").json()
    with_skipped = client.get("/api/jobs?skipped=true").json()
    exported = list(csv.DictReader(io.StringIO(client.get("/export.csv").text)))

    assert len(exported) == shown["shown"] <= with_skipped["shown"]
    assert shown["hidden"] == with_skipped["shown"] - shown["shown"]


def test_csv_export_honours_the_active_filter(client):
    everything = list(csv.DictReader(io.StringIO(client.get("/export.csv?").text)))
    filtered = list(csv.DictReader(io.StringIO(client.get("/export.csv?min_pay=70000").text)))

    assert len(filtered) < len(everything)
    assert all(int(r["salary_max"] or r["salary_min"] or 0) >= 70000 for r in filtered)
