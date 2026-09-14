"""The head: what every board's searches do, whatever board it is.

These replace a test that read the pipeline files as text and asserted a local was named
`stem`. It could only ever check that the eight boards it knew about spelled a capture path a
particular way — not that a capture was written at all, and not that a ninth board wrote one.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import scan_health
import scan_run
import scan_search
from lead import Lead


class FakePage:
    """What a board hands back, as far as the head is concerned."""

    def __init__(self, body: str = "x" * 500, success: bool = True) -> None:
        self.success = success
        self.markdown = body
        self.html = body
        self.status_code = 200 if success else 503
        self.error_message = ""


def lead(job_id: str = "1") -> Lead:
    return Lead(source="test", search_title="t", search_location="l", role_title="Dev",
                company="Co", salary="", location="Leeds", contract="", posted="",
                url=f"https://example.com/{job_id}", job_id=job_id, raw_block="")


def a_run(tmp_path: Path) -> scan_run.Run:
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    return scan_run.Run(board="test", label="Test", stamp="2026-09-14_120000",
                        health=scan_health.RunHealth("test"), raw_dir=tmp_path / "raw")


SPEC = {"board": "test", "title": "senior engineer", "location": "leeds", "url": "https://x/1"}


def drive(run, specs, fetches, **kwargs):
    kwargs.setdefault("delay", 0)
    return asyncio.run(scan_search.search(run, specs, fetches, **kwargs))


def test_every_request_leaves_a_capture_carrying_the_run_stamp(tmp_path, monkeypatch) -> None:
    # The regression: captures were once written to a deterministic name, so a scan destroyed
    # the previous scan's evidence for the same search.
    run = a_run(tmp_path)

    async def fetches(spec):
        yield scan_search.Fetched(FakePage(), [lead()], captures={"md": "a", "html": "b"})

    drive(run, [SPEC], fetches)

    written = sorted(p.name for p in (tmp_path / "raw").iterdir())
    assert written == ["senior-engineer__leeds__2026-09-14_120000.html",
                       "senior-engineer__leeds__2026-09-14_120000.md"]


def test_a_board_that_writes_no_capture_is_not_silently_allowed(tmp_path) -> None:
    run = a_run(tmp_path)

    async def fetches(spec):
        yield scan_search.Fetched(FakePage(), [lead()])          # no captures

    drive(run, [SPEC], fetches)

    assert not list((tmp_path / "raw").iterdir())
    # The request still counts: the health tally is the head's, not the board's.
    assert run.health.searches == 1


def test_several_requests_for_one_search_keep_their_captures_apart(tmp_path) -> None:
    run = a_run(tmp_path)

    async def fetches(spec):
        for page in (1, 2):
            yield scan_search.Fetched(FakePage(), [lead(str(page))],
                                      captures={"html": "x"}, suffix=f"p{page}")

    drive(run, [SPEC], fetches, pages_per_spec=2)

    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == [
        "senior-engineer__leeds__p1__2026-09-14_120000.html",
        "senior-engineer__leeds__p2__2026-09-14_120000.html",
    ]


def test_the_page_budget_stops_the_board_fetching_again(tmp_path, monkeypatch) -> None:
    # With no cap on the number of searches, this is the only thing bounding a scan's size,
    # so it is enforced by abandoning the board's generator rather than trusted to its loop.
    run = a_run(tmp_path)
    fetched = []

    async def fetches(spec):
        for page in range(10):
            fetched.append(page)
            yield scan_search.Fetched(FakePage(), [lead(str(page))], suffix=f"p{page}")

    drive(run, [SPEC], fetches, pages_per_spec=3)

    assert fetched == [0, 1, 2]
    assert len(run.leads) == 3


def test_requests_are_spaced_out(tmp_path, monkeypatch) -> None:
    # Per-host request rate is the safety property. The board cannot fetch again until the
    # head has waited, because a generator only advances when it is asked to.
    run = a_run(tmp_path)
    order: list[str] = []

    async def slept(seconds):
        order.append("slept")

    monkeypatch.setattr(scan_search.asyncio, "sleep", slept)

    async def fetches(spec):
        for page in range(3):
            order.append("fetched")
            yield scan_search.Fetched(FakePage(), [], suffix=f"p{page}")

    drive(run, [SPEC], fetches, pages_per_spec=3, delay=15)

    assert order == ["fetched", "slept", "fetched", "slept", "fetched", "slept"]


def test_an_empty_page_counts_as_a_failure_not_as_no_results(tmp_path) -> None:
    run = a_run(tmp_path)

    async def fetches(spec):
        yield scan_search.Fetched(FakePage(body=""), [])

    drive(run, [SPEC], fetches)

    assert run.health.empty == 1
    assert run.health.all_broken


def test_the_run_counts_the_searches_it_was_given(tmp_path) -> None:
    run = a_run(tmp_path)

    async def fetches(spec):
        yield scan_search.Fetched(FakePage(), [lead(spec["title"])])

    drive(run, [SPEC, {**SPEC, "title": "lead engineer"}], fetches)

    assert run.searches == 2
    assert len(run.leads) == 2
