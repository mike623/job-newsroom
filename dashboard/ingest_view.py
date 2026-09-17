"""Preview and run the downstream ingest.

The career-ops workspace is read-only to everything here except this, and this only ever acts
on a click: the timer never calls it. What the page adds over the terminal command is that you
see the lines before they are appended — the preview is `ingest_jobspy`'s own dry run, not a
second implementation of the filtering.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config
import ingest_jobspy

LOG_DIR = ROOT / "outputs" / "state" / "logs"


@dataclass
class Candidate:
    """One job that would be appended, in its own fields rather than as a rendered line.

    The line the pipeline will actually receive is kept alongside, so what the page shows and
    what gets written can never drift into two different stories about the same job.
    """
    board: str
    title: str = ""
    company: str = ""
    location: str = ""
    posted: str = ""
    url: str = ""
    line: str = ""

    @property
    def searchable(self) -> str:
        return " ".join([self.title, self.company, self.location, self.board]).lower()


@dataclass
class Preview:
    board: str
    report: str = ""                 # the report's file name, which is also its identity
    stamp: str = ""
    rows: list[Candidate] = field(default_factory=list)
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.rows)


def workspace() -> Path | None:
    try:
        return ingest_jobspy.workspace()
    except SystemExit:
        return None                  # no workspace configured; the page hides itself


def configured() -> Path:
    """The career-ops directory as config.yml names it, resolved but not followed."""
    return ingest_jobspy.checkout()


def set_workspace(location: str) -> Path:
    """Point config.yml at career-ops, and answer with the data directory that resolves to.

    Either directory may be given: career-ops' code and its data can live apart, and a checkout
    naming its data elsewhere in `.career-ops-data` is followed the way career-ops' own tooling
    follows it. What is stored is the path as given — following the marker at read time is what
    keeps this correct when the marker is later repointed — and it is stored absolute, because
    no resolver here expands `~`.

    Checked before it is written: a path resolving to no `data/pipeline.md` is not career-ops,
    and storing one would leave the page saying nothing is configured without saying why.
    """
    if os.environ.get("CAREER_OPS_WORKSPACE"):
        raise PermissionError("CAREER_OPS_WORKSPACE is set in the environment and wins over "
                              "config.yml; unset it to edit the path here")
    location = (location or "").strip()
    if not location:
        raise ValueError("no path given")

    given = Path(location).expanduser()
    if not given.is_absolute():
        given = (ROOT / given).resolve()
    try:
        resolved = ingest_jobspy.workspace(str(given))
    except SystemExit as missing:
        raise ValueError(str(missing))

    board_config.set_career_ops_workspace(str(given))
    return resolved


def preview(board: str, base: Path) -> Preview:
    """What ingesting this board's newest report would add. Writes nothing."""
    try:
        report = ingest_jobspy.latest_report(board)
    except SystemExit as missing:
        return Preview(board=board, error=str(missing))
    try:
        kept = ingest_jobspy.ingest(report, base, dry_run=True)
    except SystemExit as failure:
        return Preview(board=board, report=report.name, error=str(failure))
    return Preview(
        board=board,
        report=report.name,
        stamp=report.stem.split("_deduped_")[-1],
        rows=[Candidate(board=board, title=row["title"], company=row["company"],
                        location=row["location"], posted=row["posted"], url=row["url"],
                        line=ingest_jobspy.pipeline_line(row))
              for row in kept],
    )


def previews(boards: list[str], base: Path) -> list[Preview]:
    found = [preview(board, base) for board in boards]
    return [p for p in found if p.report or p.error and "No deduped report" not in p.error]


def candidates(found: list[Preview], board: str = "", query: str = "") -> list[Candidate]:
    """Every job about to be sent, as one list, narrowed by the page's filters.

    The filters are a way of reading a long list, not a way of choosing what to send: ingest
    appends a whole report, so the buttons always say the full count. Anything else would mean
    the pipeline receiving something other than what the person looking at it decided on.
    """
    rows = [row for preview in found for row in preview.rows]
    if board:
        rows = [row for row in rows if row.board == board]
    for term in (query or "").lower().split():
        rows = [row for row in rows if term in row.searchable]
    return rows


async def run_many(wanted: dict[str, str]) -> tuple[list[str], list[str]]:
    """Ingest several boards, one after another. Returns what was appended and what was not.

    Sequential on purpose: every board appends to the same `pipeline.md`, and `ingest_jobspy`
    holds no lock because it was written to be run by hand, one at a time. Doing them in
    parallel here would be inventing a race the file format cannot survive.

    A board whose newest report has changed since the preview is skipped, not refused wholesale
    — the other boards' lines are still exactly what was shown.
    """
    appended, skipped = [], []
    for board, expected in wanted.items():
        try:
            code, output = await run(board, expected)
        except (ValueError, SystemExit) as refused:
            skipped.append(f"{board} ({refused})")
            continue
        (appended if code == 0 else skipped).append(board if code == 0 else f"{board} (failed)")
    return appended, skipped


async def run(board: str, expected_report: str) -> tuple[int, str]:
    """Append this board's newest report downstream, as a subprocess.

    The report the page previewed is named back to it. If a scan has finished in the meantime
    the newest report is a different file, and this refuses rather than appending something
    nobody has looked at.
    """
    report = ingest_jobspy.latest_report(board)
    if expected_report and report.name != expected_report:
        raise ValueError(f"{report.name} is newer than the {expected_report} you were shown; "
                         "reload and look again before appending")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}-ingest-{board}.log"
    with log_path.open("w", encoding="utf-8") as handle:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "reed_crawler" / "ingest_jobspy.py"), str(report),
            cwd=str(ROOT), stdout=handle, stderr=asyncio.subprocess.STDOUT,
        )
        await process.wait()
    return process.returncode, log_path.read_text(encoding="utf-8")
