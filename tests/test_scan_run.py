"""The tail: what a scan writes and when it refuses, whatever board it is."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import run_record
import scan_health
import scan_lock
import scan_run
from lead import Lead


def lead(job_id: str = "1", salary: str = "", pay: int | None = None) -> Lead:
    return Lead(source="test", search_title="t", search_location="l", role_title="Dev",
                company="Co", salary=salary, location="Leeds", contract="", posted="",
                url=f"https://example.com/{job_id}", job_id=job_id, raw_block="",
                salary_min=pay, salary_max=pay)


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """A scan writes to four places. All of them belong under tmp_path here.

    `scan_run.begin` takes a real lock and writes a real run record, which is the point — they
    are what a scan is. Left pointing at the repo, these tests would file runs for a board
    called "test" into the history the dashboard reads.
    """
    monkeypatch.setattr(scan_run, "OUTPUTS", tmp_path)
    monkeypatch.setattr(run_record, "STATE", tmp_path / "state")
    monkeypatch.setattr(run_record, "RUNS_FILE", tmp_path / "state" / "runs.json")
    monkeypatch.setattr(scan_lock, "LOCK_DIR", tmp_path / "state" / "locks")
    return tmp_path


ON = {"boards": {"test": {"enabled": True}}}
OFF = {"boards": {"test": {"enabled": False}}}


def reports(outputs: Path) -> list[str]:
    return sorted(p.name for p in (outputs / "test" / "reports").iterdir())


def test_a_scan_writes_the_raw_and_deduped_reports_under_the_filename_contract(outputs) -> None:
    # <board>_<stage>_<YYYY-MM-DD>_<HHMMSS>.json — stage discovery and every aggregation in the
    # dashboard parse this shape out of the name.
    with scan_run.begin("test", ON) as run:
        run.leads.extend([lead("1"), lead("1"), lead("2")])
        run.health.record_outcome(scan_health.OK)

    assert reports(outputs) == [f"test_deduped_{run.stamp}.json", f"test_raw_{run.stamp}.json"]
    assert len(json.loads((outputs / "test" / "reports" / f"test_raw_{run.stamp}.json").read_text())) == 3
    assert len(json.loads(run.report.read_text())) == 2


def test_a_run_where_nothing_was_usable_fails(outputs) -> None:
    # A board that has started returning empty pages must not read as a board with no jobs.
    with pytest.raises(SystemExit):
        with scan_run.begin("test", ON) as run:
            run.health.record_outcome(scan_health.EMPTY)
            run.leads.append(lead("1"))

    # The evidence is still written: a failed run is the one you most need to look at.
    assert reports(outputs) == [f"test_deduped_{run.stamp}.json", f"test_raw_{run.stamp}.json"]


def test_a_run_that_asked_nothing_does_not_fail(outputs) -> None:
    # An empty spec list is not a broken board.
    with scan_run.begin("test", ON) as run:
        pass

    assert run.report.exists()


def test_a_disabled_board_refuses_unless_asked_by_hand(outputs) -> None:
    with pytest.raises(SystemExit) as refused:
        with scan_run.begin("test", OFF):
            pass

    assert "disabled in config.yml" in str(refused.value)
    assert not (outputs / "test").exists(), "a refused scan writes nothing"

    with scan_run.begin("test", OFF, allow_disabled=True) as run:
        run.health.record_outcome(scan_health.OK)

    assert run.report.exists()


def test_pay_stated_as_prose_is_parsed_and_pay_stated_as_numbers_is_left_alone(outputs) -> None:
    with scan_run.begin("test", ON) as run:
        run.leads.append(lead("prose", salary="£70,000 - £80,000 per annum"))
        run.leads.append(lead("numbers", salary="$10,000 - $750,000 per year", pay=42))
        run.health.record_outcome(scan_health.OK)

    by_id = {row["job_id"]: row for row in json.loads(run.report.read_text())}
    assert by_id["prose"]["salary_min"] == 70000
    assert by_id["numbers"]["salary_min"] == 42, "an API's own figures are not re-parsed from its prose"


def test_the_best_paid_comes_first_and_unstated_pay_last(outputs) -> None:
    with scan_run.begin("test", ON) as run:
        run.leads.extend([lead("none"), lead("high", pay=90000), lead("low", pay=30000)])
        run.health.record_outcome(scan_health.OK)

    assert [row["job_id"] for row in json.loads(run.report.read_text())] == ["high", "low", "none"]


def test_a_board_can_say_what_makes_two_rows_the_same_job(outputs) -> None:
    # Feeds key on the link where the feed states no id; email keys on the id alone.
    with scan_run.begin("test", ON, dedupe_key=lambda row: row.url) as run:
        run.leads.extend([lead("1"), lead("2")])
        run.leads[1].url = run.leads[0].url
        run.health.record_outcome(scan_health.OK)

    assert len(json.loads(run.report.read_text())) == 1


def test_a_source_too_large_to_hold_keeps_only_the_best_paid(outputs) -> None:
    with scan_run.begin("test", ON, keep=2) as run:
        run.leads.extend([lead(str(n), pay=n * 1000) for n in range(1, 6)])
        run.health.record_outcome(scan_health.OK)

    assert [row["job_id"] for row in json.loads(run.report.read_text())] == ["5", "4"]
