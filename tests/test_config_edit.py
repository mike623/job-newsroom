from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config

# Deliberately awkward: comments that must survive, a nested `enabled` belonging to a
# sub-section, a board whose block starts with a comment, and a trailing comment on the very
# line being edited.
CONFIG = """\
# The project's only input.
search:
  titles:
    primary:
      - senior software engineer

boards:
  reed:
    # Slow mode: fewer pages per run.
    enabled: true
    proximity: 50
    full_jd:
      enabled: true
      top_n: 10

  indeed:
    enabled: false   # high friction, off by default
    radius: 50

crawl:
  delay_seconds: 15
"""


@pytest.fixture
def config(tmp_path) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def enabled_of(path: Path) -> dict:
    boards = yaml.safe_load(path.read_text(encoding="utf-8"))["boards"]
    return {name: entry.get("enabled") for name, entry in boards.items()}


def test_a_board_is_switched_off_and_back_on(config):
    assert board_config.set_board_enabled("reed", False, config) is True
    assert enabled_of(config)["reed"] is False

    assert board_config.set_board_enabled("reed", True, config) is True
    assert enabled_of(config)["reed"] is True


def test_setting_what_is_already_set_changes_nothing(config):
    before = config.read_text(encoding="utf-8")

    assert board_config.set_board_enabled("reed", True, config) is False
    assert config.read_text(encoding="utf-8") == before


def test_the_rest_of_the_file_is_left_exactly_as_it_was(config):
    board_config.set_board_enabled("reed", False, config)
    text = config.read_text(encoding="utf-8")

    assert "# The project's only input." in text
    assert "    # Slow mode: fewer pages per run.\n" in text
    assert "    proximity: 50\n" in text
    assert "crawl:\n  delay_seconds: 15\n" in text


def test_a_sub_sections_own_enabled_is_not_the_one_edited(config):
    # A board's full_jd block has an `enabled` of its own, two spaces deeper.
    board_config.set_board_enabled("reed", False, config)

    parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert parsed["boards"]["reed"]["enabled"] is False
    assert parsed["boards"]["reed"]["full_jd"]["enabled"] is True


def test_a_trailing_comment_on_the_edited_line_survives(config):
    board_config.set_board_enabled("indeed", True, config)

    assert "enabled: true   # high friction, off by default" in config.read_text(encoding="utf-8")


def test_only_the_named_board_moves(config):
    board_config.set_board_enabled("indeed", True, config)

    assert enabled_of(config) == {"reed": True, "indeed": True}


def test_a_board_that_is_not_in_the_config_is_refused(config):
    with pytest.raises(ValueError):
        board_config.set_board_enabled("haystack", True, config)


def test_a_failed_edit_leaves_the_original_untouched(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"
    path.write_text(CONFIG, encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    # Pretend the line edit went wrong in a way that changes more than the one flag: the file
    # must be left alone rather than half-written, because everything reads it.
    monkeypatch.setattr(board_config.re, "match", lambda *a, **k: None)
    with pytest.raises(ValueError):
        board_config.set_board_enabled("reed", False, path)

    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "config.yml.tmp").exists()
