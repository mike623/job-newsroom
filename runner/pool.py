"""Run several board scans at once without raising any host's request rate.

Boards are separate hosts, so scanning them concurrently costs nothing: each still sees
exactly the traffic its delays were tuned for. Adding workers *within* a board is what
defeats those delays, and that is bounded here.

The per-domain limit derives from the number of configured egress identities, not from the
size of the pool. Two workers on one host are only safe if they leave from different IPs —
splitting by search term changes what is asked for, not how often. So the limit stays at one
until proxies are configured, and the safety property survives someone later making the unit
of work finer-grained.

Worth knowing: with one worker per board the pool saturates at the number of enabled boards.
Its value is queueing, visibility and cancellation, not throughput beyond that.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

QUEUED = "queued"
RUNNING = "running"
FINISHED = "finished"


def egress_identities(config: dict, board: str) -> int:
    """How many distinct source IPs this board can crawl from.

    No proxies means one: everything leaves from this machine's address.
    """
    board_config = (config.get("boards") or {}).get(board) or {}
    proxies = board_config.get("proxies") or []
    return max(1, len(proxies))


def enabled_boards(config: dict, known: list[str]) -> list[str]:
    """Boards switched on in config, in the order given."""
    boards = config.get("boards") or {}
    return [b for b in known if (boards.get(b) or {}).get("enabled")]


def load_config() -> dict:
    path = ROOT / "config.yml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


@dataclass
class Task:
    board: str
    state: str = QUEUED
    run_id: str = ""
    error: str = ""


@dataclass
class Pool:
    """Runs queued board scans, bounded per domain and overall."""

    start_scan: object                      # async (board) -> run with an .id
    max_workers: int = 3
    tasks: list[Task] = field(default_factory=list)
    _domain_locks: dict = field(default_factory=dict)
    _slots: asyncio.Semaphore | None = None

    def _domain_slot(self, board: str, config: dict) -> asyncio.Semaphore:
        if board not in self._domain_locks:
            self._domain_locks[board] = asyncio.Semaphore(egress_identities(config, board))
        return self._domain_locks[board]

    async def run(self, boards: list[str]) -> list[Task]:
        """Scan every board given, concurrently within the limits."""
        config = load_config()
        self._slots = asyncio.Semaphore(max(1, self.max_workers))
        tasks = [Task(board=b) for b in boards]
        self.tasks = tasks
        await asyncio.gather(*(self._one(t, config) for t in tasks))
        return tasks

    async def _one(self, task: Task, config: dict) -> None:
        async with self._slots:
            async with self._domain_slot(task.board, config):
                task.state = RUNNING
                try:
                    run = await self.start_scan(task.board)
                    task.run_id = getattr(run, "id", "")
                except Exception as problem:      # a failing board must not stop the others
                    task.error = str(problem)
                finally:
                    task.state = FINISHED
