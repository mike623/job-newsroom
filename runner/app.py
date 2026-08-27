"""The scan runner's HTTP surface.

One process starts every scan, so `scan_lock`'s pid test keeps meaning what it says: a lock is
held by a live process this service can signal. Split the starting across two processes and each
reads the other's live lock as stale, then scans the same board twice — doubling the request rate
every delay in the crawler exists to avoid.

So the dashboard does not spawn; it asks. A scheduler does not spawn; it asks the same endpoints.
`POST /scan-due` is the timer's whole job, and it is the same call the Schedule page's
"Run what is due now" button makes — the question is asked in one place rather than resembled in
two.

There is no authentication, and that is deliberate rather than overlooked: these endpoints start
crawls, so the service must never be published. Compose keeps it on the internal network with no
port mapping, exactly as the dashboard is kept on loopback.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from . import pool, scans
from .scans import scan_all


@asynccontextmanager
async def lifespan(_: FastAPI):
    # This process spawns every scan, so it owns the pids in the run records and is the only one
    # that can tell a scan that died from one still running.
    scans.SPAWNS_SCANS = True
    scans.reconcile()
    yield


app = FastAPI(title="job-crawler runner", lifespan=lifespan)


def _idle(boards: list[str]) -> list[str]:
    return [b for b in boards if not scans.scans.board_is_running(b)]


def _start_pool(boards: list[str]) -> None:
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))


@app.get("/health")
def health() -> dict:
    return {"ok": True, "boards": list(scans.COMMANDS)}


@app.post("/scan/{board}")
async def scan_board(board: str, trigger: str = "dashboard") -> dict:
    if board not in scans.COMMANDS:
        raise HTTPException(status_code=404, detail="unknown board")
    if scans.scans.board_is_running(board):
        raise HTTPException(status_code=409, detail=f"{board} is already being scanned")
    run = await scans.scans.start(board, trigger=trigger)
    return {"id": run.id, "board": run.board}


@app.post("/scan-all")
async def scan_all_enabled() -> dict:
    """Every enabled board, bounded per host by the pool."""
    boards = _idle(pool.enabled_boards(pool.load_config(), list(scans.COMMANDS)))
    if not boards:
        raise HTTPException(status_code=409, detail="every enabled board is already being scanned")
    _start_pool(boards)
    return {"boards": boards}


@app.post("/scan-due")
async def scan_due() -> dict:
    """What a scheduled run would scan right now.

    The timer calls this and nothing else. It asks `scan_all.due_boards` — the same question the
    launchd agent and the terminal ask — rather than holding a second idea of what is due.
    """
    boards = _idle(scan_all.due_boards(pool.load_config()))
    if not boards:
        return {"boards": []}          # nothing due is the ordinary state, not an error
    _start_pool(boards)
    return {"boards": boards}
