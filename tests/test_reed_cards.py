from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import reed_utils

# Reed renders every card's heading as a markdown link that repeats the title as the link's
# quoted title attribute. Captured naively, that quoted part rides along inside the URL.
CAPTURE = """\
## [Senior Software Engineer](https://www.reed.co.uk/jobs/senior-software-engineer/57160390?source=searchResults "Senior Software Engineer")

10 August by [SF Partners](https://www.reed.co.uk/agency/sf-partners)

* £90,000 - £95,000 per annum
* Manchester, Lancashire
* Permanent, full-time

## [Category page](https://www.reed.co.uk/jobs/software-jobs "Software jobs")
"""

SPEC = reed_utils.SearchSpec(title="senior software engineer", location="leeds", proximity=50)


def test_the_quoted_link_title_does_not_end_up_in_the_url():
    jobs = reed_utils.parse_jobs_from_markdown(CAPTURE, SPEC)
    assert len(jobs) == 1
    assert jobs[0].url == \
        "https://www.reed.co.uk/jobs/senior-software-engineer/57160390?source=searchResults"
    assert '"' not in jobs[0].url and " " not in jobs[0].url


def test_the_rest_of_the_card_still_parses():
    job = reed_utils.parse_jobs_from_markdown(CAPTURE, SPEC)[0]
    assert (job.role_title, job.company, job.job_id) == \
        ("Senior Software Engineer", "SF Partners", "57160390")
    assert (job.salary, job.location, job.posted) == \
        ("£90,000 - £95,000 per annum", "Manchester, Lancashire", "10 August")
