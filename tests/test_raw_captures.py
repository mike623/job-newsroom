from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config


def test_run_stamp_matches_the_report_filename_format() -> None:
    # generate_html_report and every latest_*() glob parse this shape out of filenames.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}", board_config.run_stamp())


def test_raw_capture_stem_keeps_scans_of_the_same_search_apart() -> None:
    first = board_config.raw_capture_stem("senior_software_engineer__leeds", "2026-08-14_140800")
    second = board_config.raw_capture_stem("senior_software_engineer__leeds", "2026-08-15_140800")

    assert first != second
    assert first == "senior_software_engineer__leeds__2026-08-14_140800"


def test_raw_capture_stem_keeps_different_searches_apart_within_one_run() -> None:
    stamp = "2026-08-14_140800"
    leeds = board_config.raw_capture_stem("senior_software_engineer__leeds", stamp)
    manchester = board_config.raw_capture_stem("senior_software_engineer__manchester", stamp)

    assert leeds != manchester
