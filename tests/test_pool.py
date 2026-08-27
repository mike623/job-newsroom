from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

from board_config import jittered
from runner import pool


# ---- the per-domain limit ----

def test_a_board_with_no_proxies_may_only_be_crawled_by_one_worker():
    assert pool.egress_identities({"boards": {"reed": {}}}, "reed") == 1
    assert pool.egress_identities({"boards": {"reed": {"proxies": []}}}, "reed") == 1
    assert pool.egress_identities({}, "reed") == 1


def test_the_limit_rises_with_configured_egress_identities():
    config = {"boards": {"reed": {"proxies": ["http://a:8080", "http://b:8080", "http://c:8080"]}}}

    assert pool.egress_identities(config, "reed") == 3


def test_enabled_boards_are_read_from_config():
    config = {"boards": {"reed": {"enabled": True}, "indeed": {"enabled": False}, "talent": {"enabled": True}}}

    assert pool.enabled_boards(config, ["reed", "totaljobs", "indeed", "talent"]) == ["reed", "talent"]


# ---- concurrency ----

class Recorder:
    """Stands in for a scan, recording how many ran at once per board."""

    def __init__(self, hold=0.05):
        self.hold = hold
        self.active: dict[str, int] = {}
        self.peak_per_board: dict[str, int] = {}
        self.peak_overall = 0
        self._live = 0
        self.started: list[str] = []

    async def __call__(self, board):
        self.started.append(board)
        self.active[board] = self.active.get(board, 0) + 1
        self._live += 1
        self.peak_per_board[board] = max(self.peak_per_board.get(board, 0), self.active[board])
        self.peak_overall = max(self.peak_overall, self._live)
        try:
            await asyncio.sleep(self.hold)
            return type("Run", (), {"id": f"{board}-run"})()
        finally:
            self.active[board] -= 1
            self._live -= 1


def test_different_boards_scan_at_the_same_time():
    recorder = Recorder()
    worker_pool = pool.Pool(start_scan=recorder, max_workers=3)

    asyncio.run(worker_pool.run(["reed", "totaljobs", "talent"]))

    assert recorder.peak_overall == 3, "separate hosts should be crawled concurrently"


def test_one_host_is_never_crawled_twice_at_once_without_proxies(monkeypatch):
    # The invariant the whole design turns on: splitting by search term does not change the
    # rate limit, because the limit is per IP.
    monkeypatch.setattr(pool, "load_config", lambda: {"boards": {"reed": {"proxies": []}}})
    recorder = Recorder()
    worker_pool = pool.Pool(start_scan=recorder, max_workers=4)

    asyncio.run(worker_pool.run(["reed", "reed", "reed", "reed"]))

    assert recorder.peak_per_board["reed"] == 1
    assert len(recorder.started) == 4, "all four still ran, just one at a time"


def test_configured_proxies_raise_the_per_host_limit(monkeypatch):
    monkeypatch.setattr(pool, "load_config",
                        lambda: {"boards": {"reed": {"proxies": ["http://a", "http://b"]}}})
    recorder = Recorder()
    worker_pool = pool.Pool(start_scan=recorder, max_workers=4)

    asyncio.run(worker_pool.run(["reed", "reed", "reed", "reed"]))

    assert recorder.peak_per_board["reed"] == 2


def test_the_worker_count_caps_overall_concurrency(monkeypatch):
    monkeypatch.setattr(pool, "load_config", lambda: {})
    recorder = Recorder()
    worker_pool = pool.Pool(start_scan=recorder, max_workers=2)

    asyncio.run(worker_pool.run(["reed", "totaljobs", "talent", "indeed"]))

    assert recorder.peak_overall == 2


def test_a_failing_board_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(pool, "load_config", lambda: {})

    async def start(board):
        if board == "reed":
            raise RuntimeError("reed exploded")
        return type("Run", (), {"id": f"{board}-run"})()

    tasks = asyncio.run(pool.Pool(start_scan=start).run(["reed", "totaljobs"]))

    failed = next(t for t in tasks if t.board == "reed")
    ok = next(t for t in tasks if t.board == "totaljobs")
    assert "exploded" in failed.error
    assert ok.run_id == "totaljobs-run"
    assert all(t.state == pool.FINISHED for t in tasks)


def test_queued_work_is_visible_with_its_state(monkeypatch):
    monkeypatch.setattr(pool, "load_config", lambda: {})
    worker_pool = pool.Pool(start_scan=Recorder(hold=0.02), max_workers=1)

    tasks = asyncio.run(worker_pool.run(["reed", "totaljobs"]))

    assert [t.board for t in tasks] == ["reed", "totaljobs"]
    assert all(t.state == pool.FINISHED for t in tasks)


# ---- jitter ----

def test_delays_vary_around_the_configured_value():
    samples = [jittered(15) for _ in range(400)]

    assert all(15 * 0.65 <= s <= 15 * 1.35 for s in samples)
    assert len(set(round(s, 3) for s in samples)) > 300, "a fixed cadence is itself a bot signal"
    assert 14 < sum(samples) / len(samples) < 16, "the average rate must be unchanged"


def test_no_delay_stays_no_delay():
    assert jittered(0) == 0.0
    assert jittered(-1) == 0.0
