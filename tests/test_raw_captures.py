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


def test_scan_entrypoints_stamp_their_raw_captures() -> None:
    # Guards the regression this fixes: a capture path built without the run stamp silently
    # overwrites the previous scan's evidence for that search.
    for name in ["run_reed_scan.py", "totaljobs_pipeline.py", "talent_pipeline.py", "indeed_pipeline.py",
                 "adzuna_pipeline.py", "haystack_pipeline.py", "linkedin_pipeline.py",
                 "aggregator_pipeline.py"]:
        source = (ROOT / "reed_crawler" / name).read_text(encoding="utf-8")
        # aggregator_pipeline writes into a per-feed directory and picks the extension from
        # the feed, so neither can be a module constant. The stem is what this guards.
        writes = re.findall(r"\((?:RAW|raw_dir) / f\"\{(\w+)\}\.[^\"]+\"\)", source)
        assert writes, f"{name}: found no raw capture writes to check"
        assert all(w == "stem" for w in writes), f"{name}: raw capture written without a run stamp: {writes}"


def test_a_capped_board_covers_every_title_and_rotates_the_places() -> None:
    """A cap below the title x location product must defer combinations, never skip them.

    The nested loop this replaced put every combination of the first title first, so a cap of
    four asked for one title in four places and never reached the other four titles.
    """
    titles, places = ["a", "b", "c", "d", "e"], ["w", "x", "y", "z"]

    today = board_config.paired(titles, places, rotation=0)
    assert set(today) == {(t, p) for t in titles for p in places}, "the full product, once"
    assert len(today) == len(set(today)) == 20

    # A cap equal to the title count still asks for every title, in four different places.
    capped = today[:len(titles)]
    assert {t for t, _ in capped} == set(titles)
    assert len({p for _, p in capped}) == len(places)

    # And tomorrow's run pairs them differently, so no combination waits forever.
    seen = {t: set() for t in titles}
    for rotation in range(len(places)):
        for title, place in board_config.paired(titles, places, rotation)[:len(titles)]:
            seen[title].add(place)
    assert all(places == sorted(found) for found in seen.values())
