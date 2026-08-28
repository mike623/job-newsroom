from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import adzuna_pipeline
import haystack_pipeline
import indeed_pipeline
import linkedin_pipeline
import reed_utils
import salary
import talent_pipeline
import totaljobs_pipeline

LEAD_TYPES = [
    reed_utils.Job,
    totaljobs_pipeline.TotaljobsLead,
    talent_pipeline.TalentLead,
    indeed_pipeline.IndeedLead,
    adzuna_pipeline.AdzunaLead,
    haystack_pipeline.HaystackLead,
    linkedin_pipeline.LinkedInLead,
]


@pytest.mark.parametrize("lead_type", LEAD_TYPES, ids=lambda t: t.__name__)
def test_records_carry_no_score(lead_type) -> None:
    names = {f.name for f in fields(lead_type)}

    assert "score" not in names
    assert "score_notes" not in names


@pytest.mark.parametrize("module", [reed_utils, totaljobs_pipeline, talent_pipeline, indeed_pipeline,
                                    adzuna_pipeline, haystack_pipeline, linkedin_pipeline],
                         ids=lambda m: m.__name__)
def test_no_board_computes_a_score(module) -> None:
    assert not hasattr(module, "score_job")
    assert not hasattr(module, "score_lead")


class Lead:
    def __init__(self, salary_min=None, salary_max=None):
        self.salary_min = salary_min
        self.salary_max = salary_max


def test_ordering_puts_the_best_paid_first_and_unstated_pay_last() -> None:
    unstated = Lead()
    low = Lead(30000, 40000)
    high = Lead(80000, 95000)
    floor_only = Lead(salary_min=60000)

    ordered = sorted([unstated, low, high, floor_only], key=salary.sort_key, reverse=True)

    assert ordered == [high, floor_only, low, unstated]


def test_reports_written_before_the_change_are_still_readable() -> None:
    # Historical report JSON still carries a score; nothing should choke on the extra key.
    legacy = json.loads('[{"role_title": "Dev", "score": 2.4, "score_notes": "+senior"}]')

    assert legacy[0]["role_title"] == "Dev"
    assert legacy[0].get("score") == 2.4
