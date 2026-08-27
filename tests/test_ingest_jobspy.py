from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import ingest_jobspy

PORTALS = """\
title_filter:
  positive: [Senior Software Engineer, Staff Software Engineer, Platform Engineer]
  negative: [Recruiter, Junior]
location_filter:
  always_allow: [United Kingdom, London, Leeds]
  allow: [Remote, United Kingdom, London, Leeds]
  block: [United States, India]
"""

PIPELINE = """\
# Job Pipeline

## Pendientes

- [ ] https://www.linkedin.com/jobs/view/1111 | Already Here | Senior Software Engineer

## Processed

- [x] https://example.com/old | Done Co | Whatever
"""


@pytest.fixture
def base(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "portals.yml").write_text(PORTALS, encoding="utf-8")
    (tmp_path / "data" / "pipeline.md").write_text(PIPELINE, encoding="utf-8")
    (tmp_path / "data" / "scan-history.tsv").write_text(
        "url\tfirst_seen\tportal\ttitle\tcompany\tstatus\tlocation\n"
        "https://www.linkedin.com/jobs/view/2222\t2026-08-01\temail-linkedin\tSenior Software Engineer\tSeen Co\tadded\tLondon\n",
        encoding="utf-8")
    return tmp_path


def report(tmp_path, rows) -> Path:
    path = tmp_path / "email_deduped_2026-08-18_120000.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def lead(**over) -> dict:
    row = {"source": "email", "role_title": "Senior Software Engineer", "company": "Acme",
           "location": "London", "posted": "2026-08-17", "url": "https://example.com/job/1"}
    row.update(over)
    return row


def test_only_relevant_rows_survive(base, tmp_path):
    path = report(tmp_path, [
        lead(),
        lead(role_title="Technical Recruiter", url="https://example.com/job/2"),
        lead(role_title="Data Scientist", url="https://example.com/job/3"),
        lead(location="San Francisco, United States", url="https://example.com/job/4"),
        lead(posted="2020-01-01", url="https://example.com/job/5"),
    ])
    kept = ingest_jobspy.ingest(path, base, dry_run=True)
    assert [k["url"] for k in kept] == ["https://example.com/job/1"]


def test_rows_already_downstream_are_not_added_twice(base, tmp_path):
    path = report(tmp_path, [
        lead(url="https://www.linkedin.com/jobs/view/1111"),   # in pipeline.md
        lead(url="https://www.linkedin.com/jobs/view/2222"),   # in scan-history.tsv
        lead(url="https://example.com/job/new"),
    ])
    kept = ingest_jobspy.ingest(path, base, dry_run=True)
    assert [k["url"] for k in kept] == ["https://example.com/job/new"]


def test_one_req_in_several_cities_is_one_entry(base, tmp_path):
    path = report(tmp_path, [
        lead(url="https://example.com/job/a", location="London"),
        lead(url="https://example.com/job/b", location="Leeds"),
    ])
    kept = ingest_jobspy.ingest(path, base, dry_run=True)
    assert len(kept) == 1


def test_entries_land_in_pending_not_at_the_end_of_the_file(base, tmp_path):
    path = report(tmp_path, [lead(url="https://example.com/job/new", company="Newco")])
    ingest_jobspy.ingest(path, base)
    text = (base / "data" / "pipeline.md").read_text(encoding="utf-8")
    added = "- [ ] https://example.com/job/new | Newco | Senior Software Engineer | London | posted: 2026-08-17"
    assert added in text
    assert text.index(added) < text.index("## Processed")

    history = (base / "data" / "scan-history.tsv").read_text(encoding="utf-8").splitlines()[-1]
    assert history.split("\t")[0] == "https://example.com/job/new"
    assert history.split("\t")[2] == "crawler-email"


def test_a_dry_run_writes_nothing(base, tmp_path):
    before = {p: p.read_bytes() for p in (base / "data").iterdir()}
    ingest_jobspy.ingest(report(tmp_path, [lead(url="https://example.com/job/new")]), base, dry_run=True)
    assert {p: p.read_bytes() for p in (base / "data").iterdir()} == before


def test_a_jobspy_csv_reads_the_same_as_a_report(base, tmp_path):
    csv_path = tmp_path / "export.csv"
    csv_path.write_text(
        "site,job_url,title,company,location,date_posted,is_remote,description\n"
        'indeed,https://example.com/job/csv,"Senior Software Engineer, Payments",Acme,London,2026-08-17,true,"a, b"\n',
        encoding="utf-8")
    ingest_jobspy.ingest(csv_path, base)
    history = (base / "data" / "scan-history.tsv").read_text(encoding="utf-8").splitlines()[-1]
    assert history.split("\t")[2] == "jobspy-indeed"
    assert history.split("\t")[3] == "Senior Software Engineer, Payments"


def test_a_missing_workspace_is_refused_rather_than_created(tmp_path):
    with pytest.raises(SystemExit):
        ingest_jobspy.workspace(str(tmp_path / "nowhere"))


def test_a_pipeline_without_a_pending_section_gets_one(base, tmp_path):
    (base / "data" / "pipeline.md").write_text("# Job Pipeline\n", encoding="utf-8")
    ingest_jobspy.ingest(report(tmp_path, [lead(url="https://example.com/job/new")]), base)
    text = (base / "data" / "pipeline.md").read_text(encoding="utf-8")
    assert "## Pending" in text and "https://example.com/job/new" in text


def test_prose_dates_are_left_out_of_a_permanent_file(base, tmp_path):
    # Crawled boards state "4 days ago", which means nothing once written down.
    path = report(tmp_path, [lead(url="https://example.com/job/new", posted="4 days ago")])
    ingest_jobspy.ingest(path, base)
    line = [l for l in (base / "data" / "pipeline.md").read_text(encoding="utf-8").splitlines()
            if "job/new" in l][0]
    assert "posted:" not in line and "4 days ago" not in line


def test_a_report_url_carrying_a_link_title_is_trimmed_to_the_href(base, tmp_path):
    path = report(tmp_path, [lead(url='https://example.com/job/1?x=y "Senior Software Engineer"')])
    kept = ingest_jobspy.ingest(path, base, dry_run=True)
    assert kept[0]["url"] == "https://example.com/job/1?x=y"


def test_the_entry_states_the_id_it_was_filed_under(base, tmp_path):
    # A sponsored Indeed link is a different URL in every mail; without the id, the dashboard
    # could never tell that the job it is showing is already in the pipeline.
    path = report(tmp_path, [dict(lead(url="https://uk.indeed.com/pagead/clk?ad=blob"),
                                  job_id="indeed-9f8e7d6c5b4a")])
    ingest_jobspy.ingest(path, base)
    line = [l for l in (base / "data" / "pipeline.md").read_text(encoding="utf-8").splitlines()
            if "pagead" in l][0]
    assert "| job_id=email-indeed-9f8e7d6c5b4a |" in line
