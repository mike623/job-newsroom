from __future__ import annotations

import lead
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import adzuna_pipeline
import board_config

SPEC = {"board": "adzuna", "title": "senior full stack developer", "location": "leeds", "url": "https://api.adzuna.com/x"}

RESULT = {
    "id": "5223456789",
    "title": "Senior Fullstack Engineer",
    "company": {"display_name": "Some Co"},
    "location": {"display_name": "Leeds, West Yorkshire", "area": ["UK", "Yorkshire", "Leeds"]},
    "salary_min": 65000.0,
    "salary_max": 80000.0,
    "salary_is_predicted": "0",
    "contract_type": "permanent",
    "contract_time": "full_time",
    "created": "2026-08-16T09:12:44Z",
    "redirect_url": "https://www.adzuna.co.uk/land/ad/5223456789?se=abc&utm_medium=api",
    "description": "Building things.",
}


# ---- the search URL ----

def test_the_search_url_carries_no_credentials() -> None:
    # It is printed, logged and written into every raw capture.
    url = board_config.adzuna_search_url("senior full stack developer", "leeds", 30)

    assert "app_id" not in url and "app_key" not in url
    assert "what=senior+full+stack+developer" in url and "where=leeds" in url
    assert "distance=30" in url


def test_the_board_builds_one_url_per_title_and_location() -> None:
    cfg = {
        "search": {"titles": {"primary": ["a", "b"]}, "locations": {"core": ["leeds"]}},
        "boards": {"adzuna": {"enabled": True, "title_groups": ["primary"], "location_groups": ["core"]}},
    }

    rows = board_config.build_board_urls(cfg, "adzuna")

    assert [r["title"] for r in rows] == ["a", "b"]
    assert all(r["url"].startswith(board_config.BASE_ADZUNA) for r in rows)


# ---- credentials ----

def test_a_scan_without_credentials_stops_with_an_explanation(monkeypatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    with pytest.raises(SystemExit) as stop:
        adzuna_pipeline.credentials({"boards": {"adzuna": {"enabled": True}}})

    assert "developer.adzuna.com" in str(stop.value)


def test_the_environment_wins_over_the_config(monkeypatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "env-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "env-key")

    assert adzuna_pipeline.credentials({"boards": {"adzuna": {"app_id": "cfg", "app_key": "cfg"}}}) == ("env-id", "env-key")


def test_a_failure_message_never_quotes_the_key() -> None:
    assert adzuna_pipeline.redact("HTTP Error 401: ...app_key=s3cret", "s3cret") == "HTTP Error 401: ...app_key=***"


# ---- parsing ----

def test_a_result_becomes_a_lead() -> None:
    lead, = adzuna_pipeline.parse_results({"results": [RESULT]}, SPEC)

    assert lead.source == "adzuna"
    assert lead.job_id == "5223456789"
    assert lead.role_title == "Senior Fullstack Engineer"
    assert lead.company == "Some Co"
    assert lead.location == "Leeds, West Yorkshire"
    assert lead.contract == "Permanent, Full time"
    assert lead.posted == "2026-08-16"
    assert lead.url == RESULT["redirect_url"]
    assert lead.search_title == SPEC["title"] and lead.search_location == SPEC["location"]


def test_pay_arrives_as_numbers_rather_than_being_parsed_back_out_of_prose() -> None:
    lead, = adzuna_pipeline.parse_results({"results": [RESULT]}, SPEC)

    assert (lead.salary_min, lead.salary_max, lead.salary_period) == (65000, 80000, "year")
    assert lead.salary == "£65,000 - £80,000 per year"


def test_an_estimated_salary_says_so() -> None:
    predicted = {**RESULT, "salary_is_predicted": "1", "salary_min": 70000.0, "salary_max": 70000.0}

    lead, = adzuna_pipeline.parse_results({"results": [predicted]}, SPEC)

    assert lead.salary == "£70,000 per year (Adzuna estimate)"


def test_an_advert_stating_no_pay_yields_no_figures() -> None:
    silent = {**RESULT, "salary_min": None, "salary_max": None}

    lead, = adzuna_pipeline.parse_results({"results": [silent]}, SPEC)

    assert (lead.salary, lead.salary_min, lead.salary_max, lead.salary_period) == ("", None, None, "")


def test_an_empty_result_set_parses_to_nothing() -> None:
    assert adzuna_pipeline.parse_results({"count": 0, "results": []}, SPEC) == []
    assert adzuna_pipeline.parse_results({}, SPEC) == []


def test_the_same_job_found_under_two_searches_is_kept_once() -> None:
    leeds = adzuna_pipeline.parse_results({"results": [RESULT]}, SPEC)
    manchester = adzuna_pipeline.parse_results({"results": [RESULT]}, {**SPEC, "location": "manchester"})

    assert len(lead.dedupe(leeds + manchester)) == 1


# ---- health ----

def test_a_broken_request_is_a_failed_search_not_an_empty_one() -> None:
    import scan_health

    body = json.dumps({"results": [RESULT], "count": 1})

    assert scan_health.classify(adzuna_pipeline.Response(True, markdown=body)) == scan_health.OK
    assert scan_health.classify(adzuna_pipeline.Response(False, error_message="403")) == scan_health.FAILED
