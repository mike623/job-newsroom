"""The one command the cron runs.

The cron used to name each board's entrypoint itself, which made `enabled` in config.yml a
lie: a board switched off still ran, and a board switched on stayed silent until someone
remembered to edit a script that is not in this repo. So the board list now comes from the
config, and the cron asks for "a scan" rather than for four particular scans.

Each board still runs as its own subprocess of its own entrypoint, so the per-board lock,
the run record and scan health all behave exactly as they do when a person runs one by hand.
Nothing here reimplements a pipeline.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from board_config import load_config
import schedule

ROOT = Path(__file__).resolve().parents[1]

# The single definition of how a board is scanned; runner/scans.py imports this so the
# dashboard button and the cron cannot drift apart.
COMMANDS = {
    "reed": ["reed_crawler/run_reed_scan.py", "--config", "config.yml"],
    "totaljobs": ["reed_crawler/totaljobs_pipeline.py", "scan", "--config", "config.yml"],
    "talent": ["reed_crawler/talent_pipeline.py", "scan", "--config", "config.yml"],
    "indeed": ["reed_crawler/indeed_pipeline.py", "scan", "--config", "config.yml"],
    "adzuna": ["reed_crawler/adzuna_pipeline.py", "scan", "--config", "config.yml"],
    "haystack": ["reed_crawler/haystack_pipeline.py", "scan", "--config", "config.yml"],
    "email": ["reed_crawler/email_pipeline.py", "scan", "--config", "config.yml"],
}

BUSY_EXIT_CODE = 75


def enabled_boards(config: dict) -> list[str]:
    boards = config.get("boards") or {}
    return [b for b in COMMANDS if (boards.get(b) or {}).get("enabled")]


def due_boards(config: dict) -> list[str]:
    """The boards a scheduled run would scan right now.

    The config says which boards may run at all; the schedule only says when. Both the timer
    and the dashboard's "run what is due" button ask this, so neither can drift into its own
    idea of what the schedule meant.
    """
    ready = set(schedule.due())
    return [b for b in enabled_boards(config) if b in ready]


def command_for(board: str, config_path: str, limit: int | None) -> list[str]:
    command = [sys.executable, *COMMANDS[board]]
    command[command.index("config.yml")] = config_path
    if limit is not None:
        command += ["--limit", str(limit)]
    return command


def main() -> int:
    ap = argparse.ArgumentParser(description="scan every board enabled in the config")
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, help="limit search pages; for smoke tests")
    ap.add_argument("--due", action="store_true",
                    help="scan only the boards the schedule says are due; what the timer calls")
    args = ap.parse_args()

    # The cron can start anywhere, so anchor a relative config path to the repo.
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = load_config(config_path)
    boards = enabled_boards(config)
    if not boards:
        print("no boards enabled in the config; nothing to scan")
        return 0

    if args.due:
        boards = due_boards(config)
        if not boards:
            print("nothing due")
            return 0

    print(f"scanning: {', '.join(boards)}")
    failed = []
    for board in boards:
        # ponytail: sequential. Boards are separate hosts so this could run them at once —
        # see runner/pool.py for how — but a nightly cron has the time and this keeps the
        # log readable. Parallelise if the run stops fitting its window.
        code = subprocess.run(command_for(board, str(config_path), args.limit), cwd=ROOT).returncode
        if code == BUSY_EXIT_CODE:
            print(f"{board}: already being scanned, skipped")
        elif code != 0:
            print(f"{board}: failed with exit code {code}")
            failed.append(board)
        else:
            print(f"{board}: done")

    if failed:
        print(f"failed boards: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
