"""The dashboard web service.

Server-rendered, single user, bound to loopback. It reads the crawler's report files and
renders them; it holds no database and no cache.
"""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from runner import pool, scans
from runner.scans import scan_all

from . import aggregate, ingest_view, pipeline, runner_client, schedule_view
from .scan_lock_view import board_lock_state

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

@asynccontextmanager
async def lifespan(_: FastAPI):
    # A dashboard killed mid-scan leaves records showing as running for ever. Only whoever
    # started those scans can judge them: with a separate runner the pids belong to it, and this
    # process must not signal pids it never owned — in another namespace a live scan reads as a
    # dead one, and every page render would mark running scans interrupted.
    if not runner_client.enabled():
        scans.SPAWNS_SCANS = True
        scans.reconcile()
    yield


app = FastAPI(title="Job Board Crawler", docs_url=None, redoc_url=None, lifespan=lifespan)


CSV_COLUMNS = [
    "board", "job_id", "role_title", "company", "location", "salary",
    "salary_min", "salary_max", "salary_period", "contract", "posted",
    "first_seen", "last_seen", "times_seen", "in_pipeline", "url",
]


@app.get("/")
def landing(request: Request):
    summaries = aggregate.summarise_all()
    timetable = schedule_view.rows(list(scans.COMMANDS), pool.load_config())
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "summaries": summaries,
            "totals": aggregate.totals(summaries),
            "scan_history": scans.history(10),
            "locks": {b: board_lock_state(b) for b in aggregate.BOARDS},
            "scannable": list(scans.COMMANDS),
            "timetable": {row.board: row for row in timetable},
            "timer": schedule_view.timer_state(),
            "scheduled": [row for row in timetable if row.enabled],
        },
    )


@app.post("/scan/{board}")
async def start_scan(board: str):
    if board not in scans.COMMANDS:
        raise HTTPException(status_code=404, detail="unknown board")
    if runner_client.enabled():
        try:
            started = await runner_client.post(f"/scan/{board}?trigger=dashboard")
        except runner_client.RunnerError as refusal:
            raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
        return RedirectResponse(f"/scan/{started['id']}", status_code=303)

    if scans.scans.board_is_running(board):
        raise HTTPException(status_code=409, detail=f"{board} is already being scanned")
    run = await scans.scans.start(board)
    return RedirectResponse(f"/scan/{run.id}", status_code=303)


@app.post("/scan-all")
async def start_all():
    """Scan every enabled board at once, bounded per host."""
    if runner_client.enabled():
        try:
            await runner_client.post("/scan-all")
        except runner_client.RunnerError as refusal:
            raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
        return RedirectResponse("/", status_code=303)

    config = pool.load_config()
    boards = [b for b in pool.enabled_boards(config, list(scans.COMMANDS))
              if not scans.scans.board_is_running(b)]
    if not boards:
        raise HTTPException(status_code=409, detail="every enabled board is already being scanned")
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))
    # The queue is visible on the overview; each scan gets its own log page.
    return RedirectResponse("/", status_code=303)


@app.get("/scan/{run_id}")
def scan_view(request: Request, run_id: str):
    run = scans.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return templates.TemplateResponse(request, "scan.html", {"run": run})


@app.get("/scan/{run_id}/log")
async def scan_log(run_id: str):
    return StreamingResponse(
        scans.scans.stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _filters(board: str, min_pay: int | None, q: str, sort: str, actioned: str = "") -> dict:
    return {"board": board, "min_pay": min_pay, "q": q, "sort": sort, "actioned": actioned}


def _annotated_jobs():
    """Every job, carrying its downstream status when that workspace is readable."""
    jobs = aggregate.all_jobs()
    statuses = pipeline.load()
    pipeline.annotate(jobs, statuses)
    return jobs, bool(statuses)


@app.get("/jobs")
def job_list(
    request: Request,
    board: str = "",
    min_pay: int | None = None,
    q: str = "",
    sort: str = Query("first_seen"),
    actioned: str = "",
):
    every, has_pipeline = _annotated_jobs()
    jobs = aggregate.select(every, board=board, min_pay=min_pay,
                            query=q, sort=sort, actioned=actioned)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": jobs,
            "boards": aggregate.BOARDS,
            "sorts": list(aggregate.SORTS),
            "filters": _filters(board, min_pay, q, sort, actioned),
            "shown": len(jobs),
            "has_pipeline": has_pipeline,
        },
    )


@app.get("/jobs/{board}/{job_id}")
def job_detail(request: Request, board: str, job_id: str):
    if board not in aggregate.BOARDS:
        raise HTTPException(status_code=404, detail="unknown board")
    job = aggregate.find_job(board, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    statuses = pipeline.load()
    pipeline.annotate([job], statuses)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"job": job, "sightings": aggregate.sightings(board, job_id), "has_pipeline": bool(statuses)},
    )


@app.get("/schedule")
def schedule_page(request: Request, changed: str = "", started: str = ""):
    return _render_schedule(request, changed=changed, started=started)


@app.post("/schedule/run-due")
async def run_due_now(request: Request):
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
        boards = started.get("boards") or []
        return RedirectResponse(f"/schedule?started={', '.join(boards) or 'nothing due'}",
                                status_code=303)

    boards = [b for b in scan_all.due_boards(pool.load_config())
              if not scans.scans.board_is_running(b)]
    if not boards:
        return RedirectResponse("/schedule?started=nothing due", status_code=303)
    worker_pool = pool.Pool(start_scan=scans.scans.start_and_wait, max_workers=len(boards))
    asyncio.create_task(worker_pool.run(boards))
    return RedirectResponse(f"/schedule?started={', '.join(boards)}", status_code=303)


@app.post("/schedule")
async def save_schedule(request: Request):
    """Save the timetable, or show what is wrong with it and save nothing.

    A half-valid schedule is worse than none: the timer would run some boards and silently
    ignore others.
    """
    boards = list(scans.COMMANDS)
    submitted = schedule_view.Submitted.parse(await request.body())
    entries, problems = schedule_view.form_to_entries(boards, submitted)
    if problems:
        return _render_schedule(request, entries=entries, problems=problems, status=400)

    schedule_view.save(entries)
    # Whether a board may run lives in config.yml, which the cron and every terminal command
    # read too; the timetable only says when. Both are edited from this one form.
    try:
        changed = schedule_view.apply_enabled(boards, submitted, pool.load_config())
    except ValueError as refused:
        return _render_schedule(request, problems={"config.yml": [str(refused)]}, status=400)
    return RedirectResponse(f"/schedule?changed={', '.join(changed)}" if changed else "/schedule",
                            status_code=303)


def _render_schedule(request: Request, entries: dict | None = None, problems: dict | None = None,
                     status: int = 200, changed: str = "", started: str = ""):
    config = pool.load_config()
    rows = schedule_view.rows(list(scans.COMMANDS), config)
    running = {row.board for row in rows if scans.scans.board_is_running(row.board)}
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "rows": rows,
            "problems": problems or {},
            "submitted": entries or {},
            "timer": schedule_view.timer_state(),
            "days": list(enumerate(schedule_view.schedule.DAY_NAMES)),
            "changed": changed,
            "started": started,
            "running": running,
            "due_now": scan_all.due_boards(config),
        },
        status_code=status,
    )


@app.get("/ingest")
def ingest_page(request: Request, board: str = "", q: str = "",
                done: str = "", added: int = 0, skipped: str = ""):
    workspace = ingest_view.workspace()
    previews = ingest_view.previews(list(scans.COMMANDS), workspace) if workspace else []
    return templates.TemplateResponse(
        request,
        "ingest.html",
        {
            "workspace": str(workspace) if workspace else "",
            "previews": previews,
            "pending": [p for p in previews if p.count],
            "rows": ingest_view.candidates(previews, board, q),
            "total": sum(p.count for p in previews),
            "filters": {"board": board, "q": q},
            "boards": [p.board for p in previews if p.count],
            "done": done,
            "added": added,
            "skipped": skipped,
        },
    )


@app.post("/ingest-all")
async def run_all_ingests(request: Request):
    """Append every board's previewed lines, in one go."""
    if ingest_view.workspace() is None:
        raise HTTPException(status_code=409, detail="no downstream workspace configured")

    form = schedule_view.Submitted.parse(await request.body())
    wanted = {board: form.get(f"report.{board}") for board in scans.COMMANDS
              if form.get(f"report.{board}")}
    if not wanted:
        return RedirectResponse("/ingest", status_code=303)

    appended, skipped = await ingest_view.run_many(wanted)
    done = ", ".join(appended)
    return RedirectResponse(f"/ingest?done={done}&added={form.get('count', 0)}"
                            + (f"&skipped={', '.join(skipped)}" if skipped else ""),
                            status_code=303)


@app.post("/ingest/{board}")
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
    added = int(form.get("count") or 0)
    return RedirectResponse(f"/ingest?done={board}&added={added}", status_code=303)


@app.get("/runs")
def run_log(request: Request, board: str = "", page: int = 1, per_page: int = 50):
    every = aggregate.runs(board)
    shown, page, pages = aggregate.page_of(every, page, max(1, min(per_page, 200)))
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": shown,
            "boards": aggregate.BOARDS,
            "board": board,
            "page": page,
            "pages": pages,
            "total": len(every),
            "per_page": per_page,
        },
    )


@app.get("/export.csv")
def export_csv(board: str = "", min_pay: int | None = None,
               q: str = "", sort: str = "first_seen", actioned: str = ""):
    """The current filter, as a spreadsheet."""
    every, _ = _annotated_jobs()
    jobs = aggregate.select(every, board=board, min_pay=min_pay,
                            query=q, sort=sort, actioned=actioned)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        writer.writerow({
            "board": job.board,
            "job_id": job.job_id,
            "first_seen": job.first_seen,
            "last_seen": job.last_seen,
            "times_seen": job.times_seen,
            "in_pipeline": "yes" if (job.pipeline and job.pipeline.present) else "no",
            **{k: job.fields.get(k, "") for k in
               ("role_title", "company", "location", "salary", "salary_min",
                "salary_max", "salary_period", "contract", "posted", "url")},
        })
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="jobs.csv"'},
    )
