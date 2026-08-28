from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import email_pipeline
import linkedin_pipeline

SPEC = {"title": "senior software engineer", "location": "London, England, United Kingdom"}

# Two cards from a captured guest fragment, with the logo images and tracking attributes
# trimmed. The first states no pay, which is the normal case for a UK card; the second is one
# that does, so the salary text is prose rather than the numbers Adzuna's API returns.
CAPTURE = """\
<li><div class="base-card base-search-card base-search-card--link job-search-card"
     data-entity-urn="urn:li:jobPosting:4435377911">
  <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/senior-software-engineer-clearing-at-alpaca-4435377911?position=1&amp;pageNum=0&amp;trackingId=VHgD4JVHuJvARYMNzl%2FqCg%3D%3D">
    <span class="sr-only"> Senior Software Engineer - Clearing </span>
  </a>
  <div class="base-search-card__info">
    <h3 class="base-search-card__title"> Senior Software Engineer - Cl&hellip; </h3>
    <h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://www.linkedin.com/company/alpacamarkets?trk=public_jobs">Alpaca</a></h4>
    <div class="base-search-card__metadata">
      <span class="job-search-card__location"> London, England, United Kingdom </span>
      <time class="job-search-card__listdate" datetime="2026-06-06">2 months ago</time>
    </div>
  </div>
</div></li>
<li><div class="base-card base-search-card base-search-card--link job-search-card"
     data-entity-urn="urn:li:jobPosting:4440241248">
  <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/staff-engineer-at-wise-4440241248?refId=abc">
    <span class="sr-only"> Staff Engineer </span>
  </a>
  <div class="base-search-card__info">
    <h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://www.linkedin.com/company/wise">Wise</a></h4>
    <span class="job-search-card__salary-info"> &pound;90,000 - &pound;110,000 </span>
    <div class="base-search-card__metadata">
      <span class="job-search-card__location"> London, United Kingdom </span>
      <time class="job-search-card__listdate--new" datetime="2026-08-27">1 day ago</time>
    </div>
  </div>
</div></li>
"""


def test_fields_come_from_the_card_html() -> None:
    first, second = linkedin_pipeline.parse_search_cards(CAPTURE, SPEC)

    # The visible <h3> title is truncated with an ellipsis; the accessible copy is not.
    assert first.role_title == "Senior Software Engineer - Clearing"
    assert first.company == "Alpaca"
    assert first.location == "London, England, United Kingdom"
    # The visible date is relative ("2 months ago"), so the datetime attribute is what is kept.
    assert first.posted == "2026-06-06"
    assert first.salary == ""
    assert second.salary_min == 90000 and second.salary_max == 110000


def test_the_per_impression_tracking_is_dropped_from_the_url() -> None:
    lead = linkedin_pipeline.parse_search_cards(CAPTURE, SPEC)[0]

    # Untouched, trackingId/refId differ on every fetch and the same advert would never dedupe.
    assert lead.url == "https://www.linkedin.com/jobs/view/4435377911"
    assert lead.job_id == "4435377911"


def test_the_same_advert_twice_is_one_lead() -> None:
    leads = linkedin_pipeline.parse_search_cards(CAPTURE + CAPTURE, SPEC)

    assert len(leads) == 4
    assert len(linkedin_pipeline.dedupe(leads)) == 2


def test_the_id_agrees_with_the_one_the_email_board_derives() -> None:
    """The same posting arrives from two boards, so the ids have to describe the same job.

    The email board forwards LinkedIn postings, and `dashboard/pipeline.py` keys downstream
    status on (board, job_id). If these two disagreed, a job actioned from an alert mail would
    read as untouched on the LinkedIn board.
    """
    lead = linkedin_pipeline.parse_search_cards(CAPTURE, SPEC)[0]

    assert email_pipeline.job_id_for("linkedin", lead.url) == f"linkedin-{lead.job_id}"


def test_a_link_that_is_not_a_posting_yields_nothing() -> None:
    # A card whose link carries no numeric tail is not a job, and inventing an id from the
    # slug would file every one of them under the same key.
    not_a_posting = CAPTURE.replace("-at-alpaca-4435377911?", "-at-alpaca?")

    assert len(linkedin_pipeline.parse_search_cards(not_a_posting, SPEC)) == 1


def test_an_error_body_is_not_read_as_zero_results() -> None:
    assert linkedin_pipeline.parse_search_cards("", SPEC) == []
    assert 429 in linkedin_pipeline.BLOCKED_STATUSES and 999 in linkedin_pipeline.BLOCKED_STATUSES
