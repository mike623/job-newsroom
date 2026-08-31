"""The dashboard's JSON API.

The pages are a React application; this is everything it reads and every action it can take.
Nothing here is a public API — it is bound to loopback along with the rest of the service, and
the schema endpoints stay off.

Actions answer with JSON rather than the 303 the Jinja forms used to need. A refusal the runner
made deliberately — 409 for a board already being scanned — still reaches the browser as that
same status, so the client can tell "busy" from "broken".
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from runner import pool, scans
from runner.scans import scan_all

from . import aggregate, ingest_view, pipeline, runner_client, schedule_view, serialise
from .scan_lock_view import board_lock_state

router = APIRouter(prefix="/api")


def _annotated_jobs():
    """Every job, carrying its downstream status and the ingest's verdict on it."""
    jobs = aggregate.all_jobs()
    statuses = pipeline.load()
    pipeline.annotate(jobs, statuses)
    pipeline.annotate_relevance(jobs, pipeline.relevance())
    return jobs, bool(statuses)


@router.get("/overview")
def overview():
    summaries = aggregate.summarise_all()
    timetable = schedule_view.rows(list(scans.COMMANDS), pool.load_config())
    locks = {board: board_lock_state(board) for board in aggregate.BOARDS}
    return {
        "summaries": [serialise.summary(s) for s in summaries],
        "totals": aggregate.totals(summaries),
        "history": [serialise.scan_run(r) for r in scans.history(10)],
        # A lock is a pid and nothing more; the client only asks whether the board is busy.
        "locks": {board: lock for board, lock in locks.items() if lock},
        "scannable": list(scans.COMMANDS),
        "timetable": {row.board: serialise.schedule_row(row) for row in timetable},
        "timer": schedule_view.timer_state(),
        "scheduled": [row.board for row in timetable if row.enabled],
    }


@router.get("/jobs")
def job_list(
    board: str = "",
    min_pay: int | None = None,
    q: str = "",
    sort: str = Query("first_seen"),
    actioned: str = "",
    skipped: bool = False,
):
    every, has_pipeline = _annotated_jobs()
    jobs = aggregate.select(every, board=board, min_pay=min_pay,
                            query=q, sort=sort, actioned=actioned, skipped=skipped)
    return {
        "jobs": [serialise.job(j) for j in jobs],
        "boards": aggregate.BOARDS,
        "sorts": list(aggregate.SORTS),
        "shown": len(jobs),
        "total": len(every),
        # What is being held back, so the checkbox can say how much it would reveal.
        "hidden": 0 if skipped else sum(1 for j in every if j.ingest_skip),
        "has_pipeline": has_pipeline,
    }


@router.get("/jobs/{board}/{job_id}")
def job_detail(board: str, job_id: str):
    if board not in aggregate.BOARDS:
        raise HTTPException(status_code=404, detail="unknown board")
    found = aggregate.find_job(board, job_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown job")
    statuses = pipeline.load()
    pipeline.annotate([found], statuses)
    return {
        "job": serialise.job(found, detail=True),
        "sightings": aggregate.sightings(board, job_id),
        "has_pipeline": bool(statuses),
    }


@router.get("/runs")
def run_log(board: str = "", page: int = 1, per_page: int = 50):
    every = aggregate.runs(board)
    shown, page, pages = aggregate.page_of(every, page, max(1, min(per_page, 200)))
    return {
        "runs": [serialise.run(r) for r in shown],
        "boards": aggregate.BOARDS,
        "board": board,
        "page": page,
        "pages": pages,
        "total": len(every),
        "per_page": per_page,
    }


# ---------------------------------------------------------------- scanning


@router.post("/scan/{board}")
async def start_scan(board: str):
    if board not in scans.COMMANDS:
        raise HTTPException(status_code=404, detail="unknown board")
    if runner_client.enabled():
        try:
            started = await runner_client.post(f"/scan/{board}?trigger=dashboard")
        except runner_client.RunnerError as refusal:
            raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
        return {"run_id": started["id"], "board": board}

    if scans.scans.board_is_running(board):
        raise HTTPException(status_code=409, detail=f"{board} is already being scanned")
    run = await scans.scans.start(board)
    return {"run_id": run.id, "board": board}


@router.post("/scan-all")
async def start_all():
    """Scan every enabled board at once, bounded per host."""
    if runner_client.enabled():
        try:
            started = await runner_client.post("/scan-all")
        except runner_client.RunnerError as refusal:
            raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
        return {"boards": started.get("boards") or []}

    config = pool.load_config()
    boards = [b for b in pool.enabled_boards(config, list(scans.COMMANDS))
              if not scans.scans.board_is_running(b)]
    if not boards:
        raise HTTPException(status_code=409, detail="every enabled board is already being scanned")
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))
    return {"boards": boards}


@router.get("/scan/{run_id}")
def scan_view(run_id: str):
    run = scans.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return serialise.scan_run(run)


@router.get("/scan/{run_id}/log")
async def scan_log(run_id: str):
    return StreamingResponse(
        scans.scans.stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- schedule


@router.get("/schedule")
def schedule_page():
    config = pool.load_config()
    rows = schedule_view.rows(list(scans.COMMANDS), config)
    return {
        "rows": [serialise.schedule_row(row) for row in rows],
        "timer": schedule_view.timer_state(),
        "days": list(schedule_view.schedule.DAY_NAMES),
        "running": [row.board for row in rows if scans.scans.board_is_running(row.board)],
        "due_now": scan_all.due_boards(config),
    }


@router.post("/schedule")
async def save_schedule(request: Request):
    """Save the timetable, or say what is wrong with it and save nothing.

    A half-valid schedule is worse than none: the timer would run some boards and silently
    ignore others.
    """
    boards = list(scans.COMMANDS)
    submitted = schedule_view.Submitted.parse(await request.body())
    entries, problems = schedule_view.form_to_entries(boards, submitted)
    if problems:
        raise HTTPException(status_code=400, detail={"problems": problems})

    schedule_view.save(entries)
    # Whether a board may run lives in config.yml, which the cron and every terminal command
    # read too; the timetable only says when. Both are edited from this one form.
    try:
        changed = schedule_view.apply_enabled(boards, submitted, pool.load_config())
    except ValueError as refused:
        raise HTTPException(status_code=400, detail={"problems": {"config.yml": [str(refused)]}})
    return {"changed": changed}


@router.post("/schedule/run-due")
async def run_due_now():
    """Do now what the timer would do at its next tick.

    Not a shortcut around the schedule: it asks `scan_all.due_boards`, the same question the
    timer asks, so pressing this proves what the timer will actually run rather than something
    that merely resembles it.
    """
    if runner_client.enabled():
        try:
            started = await runner_client.post("/scan-due")
        except runner_client.RunnerError as refusal:
            raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
        return {"boards": started.get("boards") or []}

    boards = [b for b in scan_all.due_boards(pool.load_config())
              if not scans.scans.board_is_running(b)]
    if not boards:
        return {"boards": []}
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))
    return {"boards": boards}


# ---------------------------------------------------------------- ingest


@router.get("/ingest")
def ingest_page(board: str = "", q: str = ""):
    workspace = ingest_view.workspace()
    if workspace is None:
        return {"workspace": "", "previews": [], "rows": [], "total": 0, "boards": []}
    previews = ingest_view.previews(list(scans.COMMANDS), workspace)
    return {
        "workspace": str(workspace),
        "previews": [serialise.preview(p) for p in previews],
        "rows": [serialise.candidate(row) for row in ingest_view.candidates(previews, board, q)],
        "total": sum(p.count for p in previews),
        "boards": [p.board for p in previews if p.count],
    }


@router.post("/ingest/{board}")
async def run_ingest(request: Request, board: str):
    """Append what the preview showed to the downstream pipeline. Only ever a person's click."""
    if board not in scans.COMMANDS:
        raise HTTPException(status_code=404, detail="unknown board")
    if ingest_view.workspace() is None:
        raise HTTPException(status_code=409, detail="no downstream workspace configured")

    form = schedule_view.Submitted.parse(await request.body())
    try:
        code, output = await ingest_view.run(board, form.get("report"))
    except ValueError as stale:
        raise HTTPException(status_code=409, detail=str(stale))
    if code != 0:
        raise HTTPException(status_code=500, detail=output[-400:] or "ingest failed")
    return {"appended": [board], "skipped": [], "added": int(form.get("count") or 0)}


@router.post("/ingest-all")
async def run_all_ingests(request: Request):
    """Append every board's previewed lines, in one go."""
    if ingest_view.workspace() is None:
        raise HTTPException(status_code=409, detail="no downstream workspace configured")

    form = schedule_view.Submitted.parse(await request.body())
    wanted = {board: form.get(f"report.{board}") for board in scans.COMMANDS
              if form.get(f"report.{board}")}
    if not wanted:
        return {"appended": [], "skipped": [], "added": 0}

    appended, skipped = await ingest_view.run_many(wanted)
    return {"appended": appended, "skipped": skipped, "added": int(form.get("count") or 0)}
