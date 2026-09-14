"""What a scan is, apart from asking a board's host questions.

Every board's `scan()` ended the same thirty lines: take the board's lock, stamp the run,
open a run record, apply the salary parser, dedupe, sort by pay, write the raw and deduped
reports, tell the record what was found, and let `scan_health` decide whether the run counts
as a failure. Nine copies, because each board was written by copying the last one.

Those thirty lines are where the invariants live — one scan per board, the report filename
contract, an unusable run failing rather than reporting zero results — so nine copies meant
nine places to get them right and no way to test any of them. Adding a board meant
rediscovering them from a neighbour.

The board keeps what is its own: how to reach its host, how to read a card. It hands back
leads and a verdict on each request, and this owns the rest.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from board_config import run_stamp
from lead import Lead, dedupe, identity
import run_record
import salary as salary_parser
import scan_health
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"


@dataclass
class Run:
    """One scan, while it is happening.

    A board appends to `leads` and records each request against `health`; `stamp` is what ties
    its raw captures to the report this writes.
    """
    board: str
    label: str
    stamp: str
    health: scan_health.RunHealth
    raw_dir: Path
    leads: list[Lead] = field(default_factory=list)
    searches: int = 0
    report: Path | None = None
    # Set once the body is done, for a board that writes something of its own beside the
    # reports — Reed's markdown summary is the only one.
    deduped: list[Lead] = field(default_factory=list)


def enabled(cfg: dict, board: str, allow_disabled: bool = False) -> dict:
    """The board's config block, or a refusal to scan a board switched off.

    `config.yml` decides whether a board runs at all, and it said so in three different shapes:
    some boards exited with an explanation, some exited without one, and Reed never looked. One
    check, one message. `--allow-disabled` is for a smoke test at the terminal and is never in
    `scan_all.COMMANDS`.
    """
    block = (cfg.get("boards") or {}).get(board) or {}
    if not block.get("enabled") and not allow_disabled:
        raise SystemExit(f"{board} is disabled in config.yml. "
                         "Use --allow-disabled for manual smoke tests.")
    return block


@contextmanager
def begin(board: str, cfg: dict, *, label: str = "", allow_disabled: bool = False,
          dedupe_key: Callable[[Lead], str] = identity, keep: int = 0):
    """Hold the board's lock for one scan and write its reports when the body is done.

    Yields a `Run`. Whatever the body leaves in `run.leads` is what gets written; if the body
    raises, `run_record` still records how the scan ended.
    """
    enabled(cfg, board, allow_disabled)
    with scan_lock.hold(board):
        stamp = run_stamp()
        with run_record.record(board, stamp) as findings:
            run = Run(board=board, label=label or board, stamp=stamp,
                      health=scan_health.RunHealth(board),
                      raw_dir=OUTPUTS / board / "raw")
            run.raw_dir.mkdir(parents=True, exist_ok=True)

            yield run

            # A board that states pay as numbers has already set them; only prose needs parsing.
            for lead in run.leads:
                if lead.salary and lead.salary_min is None and lead.salary_max is None:
                    salary_parser.apply_to(lead)
            deduped = sorted(dedupe(run.leads, key=dedupe_key),
                             key=salary_parser.sort_key, reverse=True)
            # `keep` is for a source that publishes more than is worth holding — the best paid
            # of what it returned, the ordering above having already decided what "best" is.
            if keep:
                deduped = deduped[:keep]
            run.deduped = deduped

            reports = OUTPUTS / board / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            raw_path = reports / f"{board}_raw_{stamp}.json"
            run.report = reports / f"{board}_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in run.leads], indent=2),
                                encoding="utf-8")
            run.report.write_text(json.dumps([x.to_dict() for x in deduped], indent=2),
                                  encoding="utf-8")

            print(f"{run.label} raw={len(run.leads)} deduped={len(deduped)}")
            findings.update(jobs=len(deduped), searches=run.searches)
            print(f"Deduped JSON: {run.report}")
            run.health.finish()
