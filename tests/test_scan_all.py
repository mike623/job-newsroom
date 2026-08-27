from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import scan_all


# ---- the config decides which boards run ----

def test_a_disabled_board_is_not_scanned():
    config = {"boards": {"reed": {"enabled": True}, "indeed": {"enabled": False}}}
    assert scan_all.enabled_boards(config) == ["reed"]


def test_a_board_missing_from_the_config_is_not_scanned():
    assert scan_all.enabled_boards({"boards": {"reed": {"enabled": True}}}) == ["reed"]
    assert scan_all.enabled_boards({}) == []


def test_every_known_board_can_be_enabled():
    config = {"boards": {b: {"enabled": True} for b in scan_all.COMMANDS}}
    assert set(scan_all.enabled_boards(config)) == set(scan_all.COMMANDS)


# ---- the command handed to each subprocess ----

def test_the_config_path_reaches_the_board_and_is_not_hardcoded():
    for board in scan_all.COMMANDS:
        command = scan_all.command_for(board, "/somewhere/else.yml", None)
        assert "config.yml" not in command
        assert command[command.index("--config") + 1] == "/somewhere/else.yml"


def test_a_limit_is_passed_through_only_when_asked_for():
    assert "--limit" not in scan_all.command_for("reed", "config.yml", None)
    assert scan_all.command_for("reed", "config.yml", 1)[-2:] == ["--limit", "1"]


def test_no_board_is_scanned_with_a_flag_that_overrides_the_config():
    # --allow-disabled exists for manual smoke tests; the cron must not use it.
    for command in scan_all.COMMANDS.values():
        assert "--allow-disabled" not in command


def test_the_dashboard_runs_the_same_commands_as_the_cron():
    from runner import scans

    assert scans.COMMANDS is scan_all.COMMANDS


# ---- the schedule decides when they run ----

def _run_main(monkeypatch, tmp_path, argv, due, enabled=("reed", "email")):
    """Run main() with the boards `due` and record which ones actually got started."""
    import schedule

    started = []
    config = tmp_path / "config.yml"
    config.write_text("boards:\n" + "".join(f"  {b}:\n    enabled: true\n" for b in enabled),
                      encoding="utf-8")
    monkeypatch.setattr(schedule, "due", lambda *a, **k: list(due))
    monkeypatch.setattr(scan_all.subprocess, "run",
                        lambda command, **kw: started.append(command[1]) or _Ok())
    monkeypatch.setattr(sys, "argv", ["scan_all.py", "--config", str(config), *argv])
    return scan_all.main(), started


class _Ok:
    returncode = 0


def test_due_scans_only_the_boards_the_schedule_asks_for(monkeypatch, tmp_path):
    code, started = _run_main(monkeypatch, tmp_path, ["--due"], due=["email"])

    assert code == 0
    assert started == ["reed_crawler/email_pipeline.py"]


def test_due_with_nothing_scheduled_is_a_quiet_success(monkeypatch, tmp_path):
    # The timer runs every few minutes and mostly has nothing to do; that is not a failure.
    code, started = _run_main(monkeypatch, tmp_path, ["--due"], due=[])

    assert (code, started) == (0, [])


def test_the_schedule_cannot_run_a_board_the_config_disabled(monkeypatch, tmp_path):
    code, started = _run_main(monkeypatch, tmp_path, ["--due"], due=["indeed"], enabled=("reed",))

    assert (code, started) == (0, [])


def test_without_due_every_enabled_board_runs(monkeypatch, tmp_path):
    code, started = _run_main(monkeypatch, tmp_path, [], due=[])

    assert code == 0
    assert len(started) == 2
