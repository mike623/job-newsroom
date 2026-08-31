"""The dashboard web service.

Single user, bound to loopback. It reads the crawler's report files and serves them as JSON to
a React client; it holds no database and no cache.

Two things are served. `/api` is every reading and every action, in `api.py`. Everything else
is the built client: `/assets` is its bundle, and any other path returns its index.html so the
client router owns the URLs — a job link pasted into a browser has to resolve, and only the
client knows what `/jobs/reed/123` means.
"""
from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from runner import scans

from . import aggregate, api, runner_client

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

# The client is a build artefact, not source, so a checkout has none until `npm run build`.
# Saying so beats an empty page that looks like a broken service.
UNBUILT = """<!doctype html><meta charset=utf-8><title>Job Newsroom</title>
<body style="font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             max-width:44em;margin:12vh auto;padding:0 24px">
<h1 style="font-size:1.1rem">The dashboard has not been built</h1>
<p>The interface is a React application built into <code>dashboard/static/</code>. Build it once:</p>
<pre style="background:#8881;padding:12px 14px;border-radius:8px">cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>Then reload. The API is already running — <code>/api/overview</code> answers.</p>
"""


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


app = FastAPI(title="Job Newsroom", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.include_router(api.router)


CSV_COLUMNS = [
    "board", "job_id", "role_title", "company", "location", "salary",
    "salary_min", "salary_max", "salary_period", "contract", "posted",
    "first_seen", "last_seen", "times_seen", "in_pipeline", "url",
]


@app.get("/export.csv")
def export_csv(board: str = "", min_pay: int | None = None,
               q: str = "", sort: str = "first_seen", actioned: str = ""):
    """The current filter, as a spreadsheet.

    A browser download rather than part of the API: the client links straight to it, so the
    file is named and saved by the browser instead of being assembled in JavaScript.
    """
    every, _ = api._annotated_jobs()
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


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def client(path: str):
    """The built client, for every path the API and the CSV did not claim.

    An unknown /api path must stay a 404 rather than quietly returning a page: a failed fetch
    reading as HTML is the kind of bug that takes an afternoon.
    """
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="unknown endpoint")
    # `..` in the path would otherwise read any file the process can. Resolve first and refuse
    # anything that lands outside the build.
    asset = (STATIC / path).resolve()
    if path and asset.is_file() and asset.is_relative_to(STATIC.resolve()):
        return FileResponse(asset)
    index = STATIC / "index.html"
    if not index.exists():
        return HTMLResponse(UNBUILT, status_code=503)
    return FileResponse(index)
