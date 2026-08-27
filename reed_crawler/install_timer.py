"""Install the one timer that asks "is anything due?".

macOS, and a LaunchAgent rather than crontab, for two reasons. The email board reads its Gmail
password out of the login keychain (`security find-generic-password`), which needs the user's
session — cron does not have one. And launchd fires a missed interval after the machine wakes,
where cron simply skips it.

The agent is written once and never edited again: it always runs `scan_all.py --due`, and every
later change of timetable is a change to `outputs/state/schedule.json`, which the dashboard owns.

    .venv/bin/python reed_crawler/install_timer.py            # write the plist, print the command
    .venv/bin/python reed_crawler/install_timer.py --install  # write it and load it
    .venv/bin/python reed_crawler/install_timer.py --uninstall
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.mikewong.job-crawler"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = ROOT / "outputs" / "state" / "logs" / "timer.log"

# Every ten minutes. The tick is cheap — it reads two JSON files and usually exits at once —
# and a coarse tick is what lets a schedule of "every 30 minutes" mean roughly that.
TICK_SECONDS = 600

# launchd starts an agent with almost no PATH. himalaya lives in Homebrew's bin, and crawl4ai
# shells out to its browser, so the agent is given the same places a terminal would look.
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def definition(interval: int = TICK_SECONDS) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "reed_crawler" / "scan_all.py"),
            "--due",
            "--config", "config.yml",
        ],
        "WorkingDirectory": str(ROOT),
        "StartInterval": interval,
        "RunAtLoad": False,          # loading the agent should not start a scan
        "EnvironmentVariables": {"PATH": PATH, "JOB_CRAWLER_TRIGGER": "scheduled"},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }


def target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def is_loaded() -> bool:
    """Whether launchd currently knows about the agent.

    The dashboard asks this: a timetable with no timer behind it is a page of good intentions,
    and that has to be visible rather than discovered a week later.

    Off a Mac there is no launchctl at all — in a container the timer is a separate service, and
    saying so is `schedule_view.timer_state`'s job. Here the honest answer is only that launchd
    is not running the agent.
    """
    try:
        result = subprocess.run(["launchctl", "print", target()], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def write(interval: int = TICK_SECONDS) -> Path:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(definition(interval)))
    return PLIST


def load() -> None:
    subprocess.run(["launchctl", "bootout", target()], capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)


def unload() -> None:
    subprocess.run(["launchctl", "bootout", target()], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="install the launchd timer that runs due scans")
    ap.add_argument("--install", action="store_true", help="load the agent as well as writing it")
    ap.add_argument("--uninstall", action="store_true", help="unload the agent and delete the plist")
    ap.add_argument("--interval", type=int, default=TICK_SECONDS, help="seconds between ticks")
    args = ap.parse_args()

    if args.uninstall:
        unload()
        PLIST.unlink(missing_ok=True)
        print(f"removed {PLIST}")
        return 0

    path = write(args.interval)
    print(f"wrote {path}")
    if args.install:
        load()
        print(f"loaded {LABEL}; it will ask for due boards every {args.interval}s")
        print(f"log: {LOG}")
        return 0

    print("\nTo load it:")
    print(f"  launchctl bootstrap gui/$UID {path}")
    print(f"\nTo run it once now:  launchctl kickstart -k {target()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
