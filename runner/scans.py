"""Start scans and follow them while they run.

A scan takes minutes, so it cannot happen inside a request. Each one is a subprocess running
exactly the command the cron runs — no reimplementation of the pipeline, no crawl4ai inside
the web process, and a crash takes the child rather than its parent.

Starting belongs to whichever process owns the resulting children: it holds the Process object,
reaps it, and is the only one whose pid comparisons mean anything. Following does not — the log
and the run record are files, so a reader in another process sees the same scan.

The scan records itself: `reed_crawler/run_record` writes the history for every scan whatever
started it, so the cron and a terminal appear alongside these. This module adds only what is
particular to a dashboard run — the captured log, and the process to stream it from.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reed_crawler"))

import run_record
import scan_all
from scan_all import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "outputs" / "state" / "logs"

# COMMANDS is the cron's own table, imported rather than repeated: a button here and the
# nightly run must be the same scan. Only scanning is exposed — anything that writes outside
# this project stays at the terminal.

RUNNING = run_record.RUNNING
DONE = run_record.DONE
FAILED = run_record.FAILED
BUSY = run_record.BUSY
INTERRUPTED = run_record.INTERRUPTED
BUSY_EXIT_CODE = 75


@dataclass
class ScanRun:
    id: str
    board: str
    status: str = RUNNING
    started: str = ""
    ended: str = ""
    exit_code: int | None = None
    trigger: str = "scheduled"
    log: str = ""
    jobs: int | None = None
    searches: int | None = None

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"{self.id}.log"

    @property
    def has_log(self) -> bool:
        # The file on disk is the evidence, not the record's `log` field: two processes write a
        # record at scan start (this one, and the child's own run_record) and the later
        # read-modify-write drops whatever the other had just added.
        return self.log_path.exists()

    @property
    def display_started(self) -> str:
        return (self.started or "")[:16].replace("T", " ")


def _as_run(raw: dict) -> ScanRun:
    known = set(ScanRun.__dataclass_fields__)
    return ScanRun(**{k: v for k, v in raw.items() if k in known})


def history(limit: int = 20) -> list[ScanRun]:
    """Every recorded scan, newest first, whatever started it.

    Reconciled on the way past, if this process is the one that spawns scans. A scheduled scan
    that is killed — the machine sleeps, launchd restarts the agent — cannot record its own
    ending, and a record left saying "running" makes the dashboard refuse to scan that board ever
    again. Doing it here rather than only at startup means the state heals itself instead of
    needing a restart.
    """
    reconcile()
    runs = [_as_run(r) for r in run_record.load()]
    runs.sort(key=lambda r: r.started or r.id, reverse=True)
    return runs[:limit]


def get(run_id: str) -> ScanRun | None:
    return next((r for r in history(10_000) if r.id == run_id), None)


def _alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


SPAWNS_SCANS = False
"""Whether this process starts scans, and so owns the pids in the run records.

`reconcile` decides a scan is dead by signalling its pid, and a pid only means something in the
namespace that created it. A dashboard in its own container asking after a runner container's pid
is asking about a stranger: the answer is "no such process" for a scan that is running perfectly
well, and the record gets marked interrupted underneath it. So judging pids is reserved for
whoever spawned them, and every other process must leave the records alone.

Claimed in the lifespan of whatever does the spawning — `runner.app` always, `dashboard.app` only
when it has no runner to delegate to.
"""


def reconcile() -> None:
    """Mark runs whose process is gone.

    A scan records its own ending, but nothing can record being killed. Without this a
    dashboard or a machine that went down leaves records showing as running for ever.

    Does nothing in a process that did not spawn the scans: see SPAWNS_SCANS.
    """
    if not SPAWNS_SCANS:
        return
    records = run_record.load()
    watched = set(scans.processes)
    changed = False
    for record in records:
        # Never touch a child this process is still watching: it has its own ending to write,
        # and the moment between its exit and that write would otherwise look like a killing.
        # No pid is not evidence of death either — only a process we can look for and find
        # gone has been proved to have stopped.
        if record.get("status") == RUNNING and record.get("id") not in watched \
                and isinstance(record.get("pid"), int) and not _alive(record["pid"]):
            record["status"] = INTERRUPTED
            record["ended"] = record.get("ended") or datetime.now().isoformat(timespec="seconds")
            changed = True
    if changed:
        run_record.save(records)


class Scans:
    """Tracks the scans this process has started."""

    def __init__(self) -> None:
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    def board_is_running(self, board: str) -> bool:
        return any(r.board == board and r.status == RUNNING for r in history(10_000))

    async def start(self, board: str, trigger: str = "dashboard") -> ScanRun:
        if board not in COMMANDS:
            raise KeyError(board)

        # Hand the identity down so the child's own record and this log are the same run.
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_id = f"{stamp}-{board}"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{run_id}.log"
        try:
            log_value = str(log_path.relative_to(ROOT))
        except ValueError:
            log_value = str(log_path)          # logs need not live under the repo

        environment = {
            **os.environ,
            "JOB_CRAWLER_RUN_STAMP": stamp,
            "JOB_CRAWLER_TRIGGER": trigger,
            "PYTHONUNBUFFERED": "1",           # otherwise the log arrives in blocks
        }
        # The child writes straight to its log rather than through a pipe this process owns.
        # A pipe would break the moment the dashboard restarted, killing the scan on its next
        # line of output; a file lets the scan outlive us, and the stream reads it either way.
        log_handle = log_path.open("w", encoding="utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, *COMMANDS[board],
                cwd=str(ROOT), env=environment,
                stdout=log_handle, stderr=asyncio.subprocess.STDOUT,
            )
        finally:
            log_handle.close()   # the child holds its own descriptor now

        started = datetime.now().isoformat(timespec="seconds")
        run = ScanRun(id=run_id, board=board, started=started, trigger=trigger, log=log_value)
        # The child writes its own status. This only claims the id and attaches the log, so a
        # viewer has something to open before the scan has written anything.
        run_record.put({"id": run_id, "board": board, "stamp": stamp, "status": RUNNING,
                        "trigger": trigger, "pid": process.pid, "started": started,
                        "log": log_value})
        self.processes[run_id] = process
        asyncio.create_task(self._watch(run, process))
        return run

    async def _watch(self, run: ScanRun, process) -> None:
        """Wait for the child, and record an ending if it could not record its own."""
        try:
            await process.wait()
        finally:
            current = get(run.id)
            if current and current.status == RUNNING:
                # The child died without recording an ending — killed, or crashed hard.
                code = process.returncode
                run_record.put({
                    "id": run.id,
                    "status": BUSY if code == BUSY_EXIT_CODE else (DONE if code == 0 else FAILED),
                    "exit_code": code,
                    "ended": datetime.now().isoformat(timespec="seconds"),
                })
            # Released last: until the outcome is written, this run is still ours to record.
            self.processes.pop(run.id, None)

    async def start_and_wait(self, board: str, trigger: str = "pool") -> ScanRun:
        """Start a scan and return once it has finished.

        The pool needs this: a slot released the moment the subprocess spawns would bound
        nothing at all.
        """
        run = await self.start(board, trigger)
        while True:
            current = get(run.id)
            if current is None or current.status != RUNNING:
                return current or run
            await asyncio.sleep(0.2)

    async def stream(self, run_id: str):
        """Server-sent events carrying the log as it is written.

        Reads the file rather than the pipe, so a reader arriving late still sees everything
        from the beginning, and several viewers can follow the same scan.
        """
        run = get(run_id)
        if run is None:
            yield "event: error\ndata: unknown run\n\n"
            return

        path = run.log_path
        for _ in range(100):  # the file appears a moment after the process starts
            if path.exists():
                break
            await asyncio.sleep(0.05)

        position = 0
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    for line in handle:
                        yield f"data: {line.rstrip()}\n\n"
                    position = handle.tell()

            current = get(run_id)
            if current is None or current.status != RUNNING:
                yield f"event: finished\ndata: {current.status if current else 'unknown'}\n\n"
                return
            await asyncio.sleep(0.5)


scans = Scans()
