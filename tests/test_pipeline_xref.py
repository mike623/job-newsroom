from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dashboard import aggregate, pipeline
from dashboard.app import app

from test_job_browser import make

PIPELINE = """\
# Job Pipeline

## Pendientes

- [ ] local:jds/reed-57217476-adria-solutions-software-engineering-manager.md | Adria | Manager
- [x] https://www.reed.co.uk/jobs/lead-software-developer/57233852?source=searchResults | Inspire
- [ ] https://www.totaljobs.com/job/senior-java-engineer/morson-edge-job107832069 | Morson
- [x] https://uk.indeed.com/viewjob?jk=abc123def456 | Some Co
- [ ] https://uk.talent.com/view?id=611275213865225891 | Kerridge
- [ ] https://www.adzuna.co.uk/jobs/details/5843030543?utm_medium=api | Adzuna direct link
- [x] https://www.adzuna.co.uk/jobs/land/ad/5841506270?se=x&utm_medium=api | Adzuna click wrapper
- [ ] https://www.linkedin.com/jobs/view/4271234567 | found by the board and forwarded by mail
- [ ] https://www.glassdoor.co.uk/job-listing/JV_KO0,9_KE10,20.htm?jl=1009 | not one of our boards
- [ ] a note with no link at all

## Processed
"""


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(PIPELINE, encoding="utf-8")
    return tmp_path


def test_identifiers_are_recovered_from_every_entry_shape(workspace):
    statuses = pipeline.load(workspace)

    assert ("reed", "57217476") in statuses          # local:jds import
    assert ("reed", "57233852") in statuses          # reed URL
    assert ("totaljobs", "107832069") in statuses    # totaljobs URL
    assert ("indeed", "abc123def456") in statuses    # indeed jk parameter
    assert ("talent", "611275213865225891") in statuses
    # Adzuna returns both link shapes for the same kind of advert.
    assert ("adzuna", "5843030543") in statuses
    assert ("adzuna", "5841506270") in statuses


def test_a_ticked_entry_counts_as_actioned(workspace):
    statuses = pipeline.load(workspace)

    assert statuses[("reed", "57233852")].done is True
    assert statuses[("reed", "57217476")].done is False
    assert statuses[("indeed", "abc123def456")].done is True


def test_entries_for_other_sources_do_not_collide_with_our_boards(workspace):
    statuses = pipeline.load(workspace)

    assert not any(board in aggregate.BOARDS for board, job_id in statuses if "1009" in job_id)


def test_a_linkedin_posting_is_recognised_for_both_boards_that_can_find_it(workspace):
    """The LinkedIn board searches for postings the email board also receives as alerts.

    Both rows are real — the same advert genuinely appears under two boards — so a pipeline
    entry has to satisfy both, or the job reads as actioned on one board and untouched on the
    other. The two ids are derived independently and have to agree on the posting.
    """
    statuses = pipeline.load(workspace)

    assert ("linkedin", "4271234567") in statuses
    assert ("email", "linkedin-4271234567") in statuses


def test_a_job_listed_twice_counts_as_actioned_if_either_entry_is_ticked(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(
        "- [ ] https://www.reed.co.uk/jobs/x/111\n"
        "- [x] https://www.reed.co.uk/jobs/x/111\n", encoding="utf-8")

    assert pipeline.load(tmp_path)[("reed", "111")].done is True


def test_a_missing_workspace_yields_nothing_rather_than_failing(tmp_path):
    assert pipeline.load(tmp_path) == {}


def test_an_unreadable_pipeline_yields_nothing(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").mkdir()  # a directory where a file should be

    assert pipeline.load(tmp_path) == {}


def test_annotate_marks_jobs_and_leaves_the_rest_absent(workspace):
    statuses = pipeline.load(workspace)
    jobs = [make(board="reed", job_id="57233852"), make(board="reed", job_id="never-seen")]

    pipeline.annotate(jobs, statuses)

    assert jobs[0].pipeline.present and jobs[0].pipeline.done
    assert not jobs[1].pipeline.present


def test_the_actioned_filter_narrows_to_what_still_needs_attention(workspace):
    statuses = pipeline.load(workspace)
    jobs = [make(board="reed", job_id="57233852"), make(board="reed", job_id="untouched")]
    pipeline.annotate(jobs, statuses)

    assert [j.job_id for j in aggregate.select(jobs, actioned="no")] == ["untouched"]
    assert [j.job_id for j in aggregate.select(jobs, actioned="yes")] == ["57233852"]
    assert len(aggregate.select(jobs, actioned="")) == 2


def test_the_workspace_is_never_written_to(workspace):
    before = {p: p.stat().st_mtime_ns for p in workspace.rglob("*") if p.is_file()}

    pipeline.load(workspace)
    client = TestClient(app)
    client.get("/jobs")
    client.get("/export.csv")

    after = {p: p.stat().st_mtime_ns for p in workspace.rglob("*") if p.is_file()}
    assert before == after


def test_the_column_hides_when_there_is_no_workspace(monkeypatch):
    monkeypatch.setattr(pipeline, "workspace", lambda: None)
    client = TestClient(app)

    response = client.get("/jobs")

    assert response.status_code == 200
    assert "not yet actioned" not in response.text


EMAIL_PIPELINE = """\
# Job Pipeline

## Pendientes

- [ ] https://www.linkedin.com/jobs/view/4455188953 | Harris Computer | Principal Software Engineer
- [x] https://uk.indeed.com/rc/clk?jk=f7f9cde5007d5654&from=ja | Certica | Senior Full Stack Engineer
- [ ] https://www.totaljobs.com/job/107857792 | Client Server | Lead Software Engineer
- [ ] https://jobright.ai/jobs/info/68a3f2b1c9 | Magnify | Staff Software Engineer

## Processed
"""


@pytest.fixture
def email_workspace(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(EMAIL_PIPELINE, encoding="utf-8")
    return tmp_path


def test_email_leads_are_recognised_by_the_id_the_board_gave_them(email_workspace):
    statuses = pipeline.load(email_workspace)
    assert statuses[("email", "linkedin-4455188953")].present
    assert statuses[("email", "indeed-f7f9cde5007d5654")].done
    assert statuses[("email", "totaljobs-107857792")].present
    assert statuses[("email", "jobright-68a3f2b1c9")].present


def test_a_forwarded_posting_still_marks_the_board_that_crawled_it(email_workspace):
    # One advert can reach the pipeline as an email lead and be sitting in Totaljobs' own
    # report under its numeric id; the line has to satisfy both.
    statuses = pipeline.load(email_workspace)
    assert statuses[("totaljobs", "107857792")].present
    assert statuses[("indeed", "f7f9cde5007d5654")].done


def test_a_stated_id_identifies_an_entry_whose_url_cannot(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "pipeline.md").write_text(
        "## Pending\n\n"
        "- [ ] https://uk.indeed.com/pagead/clk?ad=blob | job_id=email-indeed-9f8e7d6c5b4a"
        " | Profile 29 | Principal Software Engineer\n", encoding="utf-8")
    statuses = pipeline.load(tmp_path)
    assert statuses[("email", "indeed-9f8e7d6c5b4a")].present
