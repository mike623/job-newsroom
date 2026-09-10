from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import aggregator_feeds
import aggregator_pipeline
import board_config
import scan_all
import scan_health
from dashboard import aggregate

# Trimmed from the live feeds on 2026-09-08. Field names are verbatim.

DEVITJOBS = """<?xml version="1.0" encoding="UTF-8"?>
<jobs>
  <job id="60759a28a4a6990017d94bf3-W36">
    <id>60759a28a4a6990017d94bf3-W36</id>
    <title>Web portal Developer</title>
    <link>https://devitjobs.uk/jobs/EllisKnight-Web-portal-Developer</link>
    <url>https://devitjobs.uk/jobs/EllisKnight-Web-portal-Developer</url>
    <region>London</region>
    <location>Colchester,, London</location>
    <city>London</city>
    <salary>£35,000 - 40,000 per year</salary>
    <company>EllisKnight</company>
    <jobtype>Full-Time</jobtype>
    <pubdate>04.10.2023</pubdate>
    <description>&lt;p&gt;Build the &lt;b&gt;portal&lt;/b&gt;.&lt;/p&gt;</description>
  </job>
  <job id="no-url">
    <id>no-url</id>
    <title>Missing its link</title>
  </job>
</jobs>
"""

REMOTEOK = [
    {"legal": "RemoteOK API. Please link back to remoteok.com.", "last_updated": "2026-09-08"},
    {"slug": "acme-senior-engineer", "id": "1137309", "date": "2026-09-07T10:00:00+00:00",
     "company": "Acme", "position": "Senior Software Engineer", "tags": ["python", "aws"],
     "description": "<p>Work on <i>things</i>.</p>", "location": "Europe",
     "salary_min": 90000, "salary_max": 120000,
     "url": "https://remoteok.com/remote-jobs/1137309?utm_source=feed"},
]

THEMUSE = {
    "page": 1,
    "page_count": 3,
    "results": [
        {"id": 18540547, "name": "Senior/Lead Software Engineer (Angular)",
         "publication_date": "2026-09-05T23:36:38Z",
         "company": {"id": 15000196, "short_name": "exadel", "name": "Exadel"},
         "locations": [{"name": "Sofia, Bulgaria"}],
         "levels": [{"name": "Senior Level", "short_name": "senior"}],
         "contents": "<p>We are looking for a Senior Engineer.</p>",
         "refs": {"landing_page": "https://www.themuse.com/jobs/exadel/seniorlead-software-engineer"}},
        {"id": 1, "name": "No landing page", "refs": {}},
    ],
}


def spec(board: str, title: str, location: str = "") -> dict:
    return {"board": board, "title": title, "location": location, "url": "https://example.com/"}


# --- the table itself -------------------------------------------------------------------

def test_every_feed_builds_a_url_on_its_own_host() -> None:
    # A typo'd endpoint is caught here rather than by a scan that fetches somebody else.
    for name, feed in aggregator_feeds.FEEDS.items():
        cfg = {"search": {}, "boards": {name: {"enabled": True, "queries": ["anything"]}}}
        rows = board_config.build_board_urls(cfg, name)

        assert rows, f"{name}: built no URLs"
        for row in rows:
            assert row["url"].startswith("https://"), f"{name}: {row['url']}"
            assert row["url"].split("/")[2] == feed.host, f"{name}: {row['url']}"


def test_every_feed_is_scannable_and_visible() -> None:
    # The three registries cannot drift: a feed you can scan is a feed you can read.
    assert set(aggregator_feeds.FEEDS) <= set(scan_all.COMMANDS)
    assert set(aggregator_feeds.FEEDS) <= set(aggregate.BOARDS)


def test_a_feed_with_no_query_parameter_asks_once() -> None:
    # devitjobs takes the whole board in one request; configured titles must not multiply it.
    cfg = {"search": {"titles": {"primary": ["a", "b"]}, "locations": {"core": ["leeds", "york"]}},
           "boards": {"devitjobs": {"enabled": True, "title_groups": ["primary"],
                                    "location_groups": ["core"]}}}

    rows = board_config.build_board_urls(cfg, "devitjobs")

    assert len(rows) == 1
    assert rows[0]["title"] == "DevITjobs UK"


def test_a_vocabulary_feed_asks_once_per_query_and_location() -> None:
    cfg = {"search": {"locations": {"core": ["leeds", "york"]}},
           "boards": {"themuse": {"enabled": True, "location_groups": ["core"],
                                  "queries": ["Software Engineering", "Data Science"]}}}

    rows = board_config.build_board_urls(cfg, "themuse")

    assert len(rows) == 4
    assert "category=Software+Engineering" in rows[0]["url"]
    assert "location=leeds" in rows[0]["url"]


def test_a_feed_that_must_be_narrowed_refuses_to_scan_the_whole_board() -> None:
    # The Muse is 411,049 postings across 20,553 pages: unqualified is a mistake, not a wait.
    cfg = {"search": {}, "boards": {"themuse": {"enabled": True}}}

    with pytest.raises(SystemExit) as refused:
        board_config.build_board_urls(cfg, "themuse")

    assert "boards.themuse.queries" in str(refused.value)


def test_max_pages_per_run_caps_the_number_of_queries() -> None:
    cfg = {"search": {}, "boards": {"themuse": {"enabled": True, "max_pages_per_run": 1,
                                                "queries": ["one", "two", "three"]}}}

    assert len(board_config.build_board_urls(cfg, "themuse")) == 1


# --- one record becomes one lead --------------------------------------------------------

def parse(board: str, payload, title: str = "", location: str = ""):
    feed = aggregator_feeds.FEEDS[board]
    leads, _ = aggregator_pipeline.leads_from(payload, spec(board, title or feed.label, location), feed)
    return leads


def test_devitjobs_record_becomes_a_lead() -> None:
    leads = parse("devitjobs", ElementTree.fromstring(DEVITJOBS))

    assert len(leads) == 1, "the record with no link should be dropped, not guessed at"
    lead = leads[0]
    assert lead.job_id == "60759a28a4a6990017d94bf3-W36"
    assert lead.role_title == "Web portal Developer"
    assert lead.company == "EllisKnight"
    assert lead.location == "London"
    assert lead.url == "https://devitjobs.uk/jobs/EllisKnight-Web-portal-Developer"
    assert lead.raw_block == "Build the portal ."


def test_devitjobs_salary_prose_is_parsed_into_numbers() -> None:
    # The one feed of the set quoting pay as advertiser text rather than as numbers.
    lead = parse("devitjobs", ElementTree.fromstring(DEVITJOBS))[0]

    assert (lead.salary_min, lead.salary_max, lead.salary_period) == (35000, 40000, "year")


def test_devitjobs_posted_date_is_rewritten_as_iso() -> None:
    # The feed stamps "04.10.2023"; anything downstream reads a date as ISO or not at all.
    assert parse("devitjobs", ElementTree.fromstring(DEVITJOBS))[0].posted == "2023-10-04"


def test_remoteok_skips_the_legal_notice_element() -> None:
    leads = parse("remoteok", REMOTEOK)

    assert len(leads) == 1
    assert leads[0].role_title == "Senior Software Engineer"


def test_remoteok_pay_arrives_as_numbers_and_is_not_reparsed() -> None:
    lead = parse("remoteok", REMOTEOK)[0]

    assert (lead.salary_min, lead.salary_max, lead.salary_period) == (90000, 120000, "year")
    assert lead.salary == "$90,000 - $120,000 per year"


def test_a_posting_url_carries_no_tracking_parameters() -> None:
    # Left on, the same posting is a different URL whenever the feed changes that value,
    # and dedup never fires.
    assert parse("remoteok", REMOTEOK)[0].url == "https://remoteok.com/remote-jobs/1137309"


def test_themuse_record_becomes_a_lead() -> None:
    leads = parse("themuse", THEMUSE, title="Software Engineering", location="leeds")

    assert len(leads) == 1, "the record with no landing page should be dropped"
    lead = leads[0]
    assert lead.job_id == "18540547"
    assert lead.company == "Exadel"
    assert lead.location == "Sofia, Bulgaria"
    assert lead.contract == "Senior Level"
    assert lead.search_title == "Software Engineering"
    assert lead.search_location == "leeds"


def test_themuse_asks_for_another_page_until_the_last_one() -> None:
    # Requesting the page after the last returns a body short enough to read as empty-body,
    # which would classify a finished search as a broken board.
    feed = aggregator_feeds.FEEDS["themuse"]
    rows = feed.rows(THEMUSE)

    assert feed.more(THEMUSE, 1, rows) is True
    assert feed.more(THEMUSE, 3, rows) is False
    assert feed.more(THEMUSE, 1, []) is False


def test_an_empty_payload_parses_to_nothing() -> None:
    assert parse("remoteok", []) == []
    assert parse("themuse", {"results": [], "page_count": 0}) == []
    assert parse("devitjobs", ElementTree.fromstring("<jobs></jobs>")) == []


def test_the_same_job_found_twice_is_kept_once() -> None:
    twice = parse("remoteok", REMOTEOK) + parse("remoteok", REMOTEOK)

    assert len(aggregator_pipeline.dedupe(twice)) == 1


# --- health -----------------------------------------------------------------------------

def test_a_broken_request_is_a_failed_search_not_an_empty_one() -> None:
    body = json.dumps(REMOTEOK)

    assert scan_health.classify(aggregator_pipeline.Response(True, markdown=body)) == scan_health.OK
    assert scan_health.classify(aggregator_pipeline.Response(False, status_code=429)) == scan_health.FAILED
