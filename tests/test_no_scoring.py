from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import adzuna_pipeline
import email_pipeline
import haystack_pipeline
import aggregator_pipeline
import indeed_pipeline
import lead
import linkedin_pipeline
import reed_utils
import salary
import talent_pipeline
import totaljobs_pipeline

def test_records_carry_no_score() -> None:
    # One type now, shared by every board — see reed_crawler/lead.py.
    names = {f.name for f in fields(lead.Lead)}

    assert "score" not in names
    assert "score_notes" not in names


@pytest.mark.parametrize("module", [reed_utils, totaljobs_pipeline, talent_pipeline, indeed_pipeline,
                                    adzuna_pipeline, haystack_pipeline, linkedin_pipeline,
                                    aggregator_pipeline, email_pipeline],
                         ids=lambda m: m.__name__)
def test_no_board_declares_a_lead_type_of_its_own(module) -> None:
    # The report row is a contract dashboard/aggregate.py reads back; it is stated once.
    declared = [name for name, value in vars(module).items()
                if isinstance(value, type) and name.endswith(("Lead", "Job")) and value is not lead.Lead]

    assert not declared, f"{module.__name__} declares {declared}"


@pytest.mark.parametrize("module", [reed_utils, totaljobs_pipeline, talent_pipeline, indeed_pipeline,
                                    adzuna_pipeline, haystack_pipeline, linkedin_pipeline,
                                    aggregator_pipeline],
                         ids=lambda m: m.__name__)
def test_no_board_computes_a_score(module) -> None:
    assert not hasattr(module, "score_job")
    assert not hasattr(module, "score_lead")


class PayOnly:
    def __init__(self, salary_min=None, salary_max=None):
        self.salary_min = salary_min
        self.salary_max = salary_max


def test_ordering_puts_the_best_paid_first_and_unstated_pay_last() -> None:
    unstated = PayOnly()
    low = PayOnly(30000, 40000)
    high = PayOnly(80000, 95000)
    floor_only = PayOnly(salary_min=60000)

    ordered = sorted([unstated, low, high, floor_only], key=salary.sort_key, reverse=True)

    assert ordered == [high, floor_only, low, unstated]


def test_reports_written_before_the_change_are_still_readable() -> None:
    # Historical report JSON still carries a score; nothing should choke on the extra key.
    legacy = json.loads('[{"role_title": "Dev", "score": 2.4, "score_notes": "+senior"}]')

    assert legacy[0]["role_title"] == "Dev"
    assert legacy[0].get("score") == 2.4
