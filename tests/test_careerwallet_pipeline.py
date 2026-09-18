from __future__ import annotations

import lead
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import board_config
import careerwallet_pipeline

SPEC = {"title": "full stack developer", "location": "London"}

# Two cards from a captured results page, with the logo images and theme wrappers trimmed. The
# title link and the "Apply Now" button both point at /stats/jbe/ — the encrypted, per-send
# tracker that makes this board's own alert mail unusable — so only the "Read more" link names
# the advert. The second card quotes pay inside the description snippet, which is the only
# place this board ever states it.
CAPTURE = """\
<div class="job-list">
  <div class="job-list-details"><div class="job-list-info">
    <div class="job-list-title"><h5><a
       data-id="https://thecareerwallet.com/apply/full-stack-developer-aladdin-trading-21119484582973157"
       href="https://thecareerwallet.com/stats/jbe/eyJpdiI6IjU4R0dXeVNxVVJyTHBkdXp3aHhPOFE9PSJ9/MTE5MjM5MQ=="
       title="Full Stack Developer, Aladdin Trading Engineering, Associate">
       Full Stack Developer, Aladdin Trading Engineering, Associate</a></h5></div>
    <div class="job-list-option">
      <ul><li><span>via</span><a href="https://thecareerwallet.com/company/j-verscom-303">Hackajob Ltd</a></li>
          <li><i class="fas fa-map-marker-alt pr-1"></i>London</li></ul>
      <p><i class="fas fa-suitcase pr-1"></i>hackajob is partnering directly with BlackRock&hellip;</p>
      <a href="https://thecareerwallet.com/job/full-stack-developer-aladdin-trading-21119484582973157">Read more</a>
    </div>
  </div></div>
  <div class="job-list-favourite-time">
    <a class="btn" href="https://thecareerwallet.com/stats/jbe/eyJpdiI6IjZhd0ZMIn0=/MTE5MjM5MQ==">Apply Now</a>
    <span>22 hours ago</span>
  </div>
</div>
<div class="job-list">
  <div class="job-list-details"><div class="job-list-info">
    <div class="job-list-title"><h5><a
       href="https://thecareerwallet.com/stats/jbe/eyJpdiI6InRQOXRmIn0=/MTE5MjM5MQ=="
       title="Senior Full Stack Developer (Kotlin, React)">
       Senior Full Stack Developer (Kotlin, React)</a></h5></div>
    <div class="job-list-option">
      <ul><li><span>via</span><a href="https://thecareerwallet.com/company/salt-77">Salt</a></li>
          <li><i class="fas fa-map-marker-alt pr-1"></i>Manchester</li></ul>
      <p>&pound;57,000 to 72,000 GBP Bonus Hybrid working&hellip;</p>
      <a href="https://thecareerwallet.com/job/senior-full-stack-developer-kotlin-react-86056154292074572">Read more</a>
    </div>
  </div></div>
  <div class="job-list-favourite-time"><span>11 hours ago</span></div>
</div>
"""


def test_fields_come_from_the_card_html() -> None:
    first, second = careerwallet_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert first.role_title == "Full Stack Developer, Aladdin Trading Engineering, Associate"
    assert first.company == "Hackajob Ltd"
    # The company's <li> carries no icon, so the location is the one the map marker labels.
    assert first.location == "London"
    assert second.location == "Manchester"
    # The site states no absolute date on a card.
    assert first.posted == "22 hours ago"
    # Pay is not a field on this board: an empty salary here is the board, not a parse failure.
    assert first.salary == "" and second.salary == ""


def test_the_advert_is_taken_from_the_read_more_link_not_the_tracker() -> None:
    """The tracker is a Laravel envelope with a fresh IV per render, so it is never an identity.

    Keeping it would file the same advert under a new URL on every scan, and the whole reason
    this board is crawled rather than read out of its alert mail would be lost.
    """
    first, second = careerwallet_pipeline.parse_search_cards(CAPTURE, SPEC)

    assert first.url == ("https://thecareerwallet.com/job/"
                         "full-stack-developer-aladdin-trading-21119484582973157")
    assert "stats/jbe" not in first.url and "stats/jbe" not in second.url
    assert first.job_id == "21119484582973157"
    assert second.job_id == "86056154292074572"


def test_the_same_advert_twice_is_one_lead() -> None:
    leads = careerwallet_pipeline.parse_search_cards(CAPTURE + CAPTURE, SPEC)

    assert len(leads) == 4
    assert len(lead.dedupe(leads)) == 2


def test_the_id_agrees_with_the_one_the_downstream_pipeline_recovers() -> None:
    """`dashboard/pipeline.py` keys downstream status on (board, job_id).

    The slug in front of the id is free text, so the two have to agree on where the id starts
    or a job actioned downstream would read as untouched on the board.
    """
    import importlib
    pipeline = importlib.import_module("dashboard.pipeline")
    first = careerwallet_pipeline.parse_search_cards(CAPTURE, SPEC)[0]

    assert ("careerwallet", first.job_id) in pipeline._identify(f"- [ ] {first.url}")


def test_a_card_with_no_canonical_link_yields_nothing() -> None:
    # Only the tracker: nothing states the advert's id, and hashing the tracker would report
    # the job as new on every scan.
    tracked_only = CAPTURE.replace(
        '<a href="https://thecareerwallet.com/job/'
        'full-stack-developer-aladdin-trading-21119484582973157">Read more</a>', "")

    assert len(careerwallet_pipeline.parse_search_cards(tracked_only, SPEC)) == 1


def test_an_error_body_is_not_read_as_zero_results() -> None:
    assert careerwallet_pipeline.parse_search_cards("", SPEC) == []


def test_the_search_uses_the_field_names_the_site_actually_filters_on() -> None:
    """`q=` is accepted and ignored — the same rows come back for `nurse` and for a developer
    title — so the form's own `search`/`location`/`Radius` are what a real search sends."""
    url = board_config.careerwallet_search_url("full stack developer", "London", 40)

    assert "search=full+stack+developer" in url
    assert "location=London" in url and "Radius=40" in url
    # Page one is the bare URL; later pages add the parameter the site's own links use.
    assert "page=" not in url
    assert board_config.careerwallet_search_url("x", "y", 40, 3).endswith("&page=3")
