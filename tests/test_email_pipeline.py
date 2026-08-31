from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import email_pipeline as email

# Bodies trimmed from real alert mail: enough structure for the block splitter, with the
# footers kept because rejecting them is half of what the URL rules are for.
INDEED_ALERT = """\
Indeed Job Alert

Senior Software Engineer
Profile 29 Ltd - Remote
£70,000 - £85,000 a year
Easily apply
https://uk.indeed.com/rc/clk?jk=abc123def456&from=alert

Lead Backend Engineer
Nutshell Ltd - Leeds
Actively recruiting
https://uk.indeed.com/viewjob?jk=999888777&from=alert

See all jobs
https://uk.indeed.com/jobs?q=software+engineer
Unsubscribe: https://subscriptions.indeed.com/unsubscribe?x=1
"""

LINKEDIN_ALERT = """\
Your job alert for software engineer

Staff Software Engineer
Monzo
London, England, United Kingdom
Be an early applicant
View job: https://www.linkedin.com/comm/jobs/view/4231567890/?trackingId=Zx%2F1&refId=abc

See all jobs: https://www.linkedin.com/comm/jobs/search-results/?keywords=engineer
"""

TOTALJOBS_DIGEST = """\
Check out your latest matches

Principal Engineer
https://click.totaljobsmail.com/f/a/tracked-id/AAB/
Kubrick Group
Manchester
Permanent
£90,000 - £100,000

Follow us https://x.com/totaljobs
"""

HAYSTACK_ALERT = """\
5 NEW JOBS MATCHING YOUR SEARCH

\U0001f525 8 hours agoTechnology
Principal Engineer \u2013 Product Safety

\U0001f3e2 BAE Systems

\U0001f4cd Bristol Area, United Kingdom \U0001f1ec\U0001f1e7  \u2022  \U0001f4b0 GBP 60,000/yr

Apply Now \u2192
https://haystack.cv/go?j=4c036fdf-76b6-42b9-a2f8-a79914c2e43a&s=searches-email&sub=SUBSCRIBER&u=https%3A%2F%2Fclick.appcast.io%2Ft%2Fblob&t=Principal%20Engineer&c=BAE%20Systems

Browse All Jobs https://haystack.cv/jobs?src=searches-email
Unsubscribe from these emails https://haystack.cv/unsubscribe?token=JWT
"""


def test_url_rules_keep_postings_and_drop_search_and_footer():
    assert email.allows_url("indeed", "https://uk.indeed.com/rc/clk?jk=abc123")
    assert not email.allows_url("indeed", "https://uk.indeed.com/jobs?q=software+engineer")
    assert not email.allows_url("indeed", "https://subscriptions.indeed.com/unsubscribe?x=1")

    assert email.allows_url("linkedin", "https://www.linkedin.com/comm/jobs/view/42/?t=x")
    assert not email.allows_url("linkedin", "https://www.linkedin.com/comm/jobs/search-results/?k=a")

    assert email.allows_url("totaljobs", "https://www.totaljobs.com/job/12345")
    assert not email.allows_url("totaljobs", "https://www.totaljobs.com/jobs/software-engineer")
    assert not email.allows_url("totaljobs", "https://x.com/totaljobs")

    # A URL belonging to another provider is not this provider's posting.
    assert not email.allows_url("jobright", "https://www.linkedin.com/jobs/view/42")


def test_per_recipient_tracking_is_stripped_so_dedup_works():
    magic = ("https://www.totaljobs.com/v2/magiclink/exchange?magicLink=eyJhbGciOi.JWT.sig"
             "&returnUrl=%2Fjob%2F98765432%2Fapplication%2Fredirection%3Fsource%3Demail")
    assert email.unwrap_magic_link(magic) == "https://www.totaljobs.com/job/98765432"
    assert email.unwrap_magic_link("https://www.linkedin.com/comm/jobs/view/42/?trackingId=z") \
        == "https://www.linkedin.com/jobs/view/42"
    assert email.unwrap_magic_link("https://jobright.ai/jobs/info/abc123?utm_source=email") \
        == "https://jobright.ai/jobs/info/abc123"


def test_job_id_is_provider_scoped_and_stable():
    assert email.job_id_for("linkedin", "https://www.linkedin.com/jobs/view/42") == "linkedin-42"
    assert email.job_id_for("totaljobs", "https://www.totaljobs.com/job/98765432") == "totaljobs-98765432"
    assert email.job_id_for("indeed", "https://uk.indeed.com/rc/clk?jk=abc123&from=alert") == "indeed-abc123"
    hashed = email.job_id_for("jobright", "https://jobright.ai/jobs/info/xyz")
    assert hashed == email.job_id_for("jobright", "https://jobright.ai/jobs/info/xyz")

WTTJ_ALERT = """\
There are new jobs matching your search preferences, Namie!

whiteworth

lead generation and software for the real estate industry.

  full-stack software engineer (long-term contract)
 salary: \u00a385-101k
 remote (within the uk) or london    (https://u9255466.ct.sendgrid.net/ls/click?upn=JOB1)

metabase

open-source business intelligence tool

  engineering manager
 salary above your minimum
 london    (https://u9255466.ct.sendgrid.net/ls/click?upn=JOB2)    see all top matches (https://u9255466.ct.sendgrid.net/ls/click?upn=FOOTER)
"""


def leads_for(label, provider, subject, sender, body):
    envelope = {"id": "1", "subject": subject, "from": {"addr": sender}, "date": "2026-08-18 08:01+00:00"}
    return email.leads_from_message({"label": label, "provider": provider}, envelope, body, 12)


def test_indeed_digest_gives_each_job_its_own_fields():
    leads, template = leads_for("job/discovery/indeed", "indeed",
                                "Senior Software Engineer at Profile 29 Ltd and 1 more new job",
                                "donotreply@match.indeed.com", INDEED_ALERT)
    assert template == "indeed-job-alert"
    assert [(l.role_title, l.company, l.location) for l in leads] == [
        ("Senior Software Engineer", "Profile 29 Ltd", "Remote"),
        ("Lead Backend Engineer", "Nutshell Ltd", "Leeds"),
    ]
    assert leads[0].posted == "2026-08-18"
    assert leads[0].job_id == "indeed-abc123def456"
    # The search link and the unsubscribe footer are not jobs.
    assert len(leads) == 2


def test_linkedin_alert_reads_title_company_location():
    leads, template = leads_for("job/discovery/linkedin", "linkedin",
                                "Staff Software Engineer at Monzo: up to £150K/year",
                                "jobalerts-noreply@linkedin.com", LINKEDIN_ALERT)
    assert template == "linkedin-job-alert"
    assert len(leads) == 1
    assert (leads[0].role_title, leads[0].company, leads[0].location) == \
        ("Staff Software Engineer", "Monzo", "London, England, United Kingdom")
    assert leads[0].url == "https://www.linkedin.com/jobs/view/4231567890"


def test_haystack_alert_reads_the_emoji_labelled_card():
    leads, template = leads_for("job/discovery/haystack", "haystack",
                                "BAE Systems is hiring Principal Engineer \u2013 in Bristol Area",
                                "alerts@alerts.haystack.cv", HAYSTACK_ALERT)
    assert template == "haystack-alert"
    assert len(leads) == 1                      # the browse and unsubscribe links are not jobs
    assert (leads[0].role_title, leads[0].company, leads[0].location) == \
        ("Principal Engineer \u2013 Product Safety", "BAE Systems", "Bristol Area, United Kingdom")


def test_a_haystack_advert_has_one_id_however_it_was_linked():
    """The board finds /jobs/<uuid>; a mail links /go?j=<uuid> with per-recipient parameters.

    Both reduce to the advert, and the email board's id is the Haystack board's id with the
    provider prefix — or a job actioned from a mail reads as untouched on the board.
    """
    import haystack_pipeline

    mailed = "https://haystack.cv/go?j=4c036fdf-76b6-42b9-a2f8-a79914c2e43a&sub=ME&t=X"
    listed = "https://haystack.cv/jobs/4c036fdf-76b6-42b9-a2f8-a79914c2e43a"

    assert email.unwrap_magic_link(mailed) == listed
    assert email.job_id_from_url(mailed) == email.job_id_from_url(listed)
    assert email.job_id_from_url(listed) == f"haystack-{haystack_pipeline.haystack_job_id(listed)}"


def test_welcometothejungle_alert_reads_the_card_above_the_link(monkeypatch):
    """Its cards state pay on their own line, and the link ends the location line."""
    destinations = {
        "JOB1": "https://app.otta.com/jobs/ypGMjG1e?token=JWT&position=1",
        "JOB2": "https://app.otta.com/jobs/IEweh3RS?token=JWT&position=2",
        "FOOTER": "https://app.otta.com/account-settings/email-notifications?token=JWT",
    }
    monkeypatch.setattr(email, "_location", lambda url: destinations[url.split("upn=")[1]])
    leads, template = leads_for("job/discovery/welcometothejungle", "welcometothejungle",
                                "New match: Full-Stack Software Engineer at Whiteworth",
                                "help@welcometothejungle.com", WTTJ_ALERT)
    assert template == "welcometothejungle-alert"
    # The settings link the footer wraps is not a job.
    assert [(l.role_title, l.company, l.location, l.url, l.job_id) for l in leads] == [
        ("full-stack software engineer (long-term contract)", "whiteworth",
         "remote (within the uk) or london", "https://app.otta.com/jobs/ypGMjG1e",
         "welcometothejungle-ypGMjG1e"),
        ("engineering manager", "metabase", "london",
         "https://app.otta.com/jobs/IEweh3RS", "welcometothejungle-IEweh3RS"),
    ]


def test_welcometothejungle_sign_in_token_is_stripped():
    # The token in a job link signs this recipient in; kept, every mail would name a new job.
    assert email.unwrap_magic_link("https://app.otta.com/jobs/ypGMjG1e?token=JWT&position=1") \
        == "https://app.otta.com/jobs/ypGMjG1e"
    assert email.allows_url("welcometothejungle", "https://app.otta.com/jobs/ypGMjG1e")
    assert not email.allows_url("welcometothejungle",
                                "https://app.otta.com/account-settings/email-notifications")


def test_totaljobs_tracker_is_resolved_before_the_lead_is_kept(monkeypatch):
    monkeypatch.setattr(email, "_location", lambda url: (
        "https://www.totaljobs.com/v2/magiclink/exchange?magicLink=JWT&returnUrl=%2Fjob%2F55512345"))
    leads, template = leads_for("job/discovery/totaljobs", "totaljobs",
                                "3 new jobs that match your search",
                                "alerts@totaljobsmail.com", TOTALJOBS_DIGEST)
    assert template == "totaljobs-search-digest"
    assert [(l.role_title, l.company, l.location, l.url) for l in leads] == [
        ("Principal Engineer", "Kubrick Group", "Manchester", "https://www.totaljobs.com/job/55512345"),
    ]


def test_unresolvable_tracker_yields_no_lead(monkeypatch):
    # A tracker that will not resolve is a link we cannot attribute to a posting; inventing a
    # lead from the tracker URL would put an unusable row in the report.
    monkeypatch.setattr(email, "_location", lambda url: "")
    leads, _ = leads_for("job/discovery/totaljobs", "totaljobs", "3 new jobs",
                         "alerts@totaljobsmail.com", TOTALJOBS_DIGEST)
    assert leads == []


def test_sender_beats_the_label_it_was_filed_under():
    leads, template = leads_for("job/discovery/totaljobs", "totaljobs",
                                "Your job alert", "jobalerts-noreply@linkedin.com", LINKEDIN_ALERT)
    assert template == "linkedin-job-alert"
    assert leads[0].url.startswith("https://www.linkedin.com/jobs/view/")


def test_digest_subject_is_not_attributed_to_every_job():
    leads, _ = leads_for("job/discovery/indeed", "indeed",
                         "Acme Ltd is hiring for Principal Engineer",
                         "donotreply@match.indeed.com", INDEED_ALERT)
    assert all(l.company != "Acme Ltd" for l in leads)


def test_unrecognized_template_still_reports_the_url_without_inventing_fields():
    body = "Something new\n\nhttps://uk.indeed.com/viewjob?jk=zzz111\n"
    leads, template = leads_for("job/discovery/indeed", "indeed", "New role for you",
                                "donotreply@match.indeed.com", body)
    assert template == ""
    assert len(leads) == 1
    assert leads[0].role_title == "Job lead (email)"
    assert leads[0].company == "Indeed"


def test_dedupe_keeps_one_row_per_posting():
    rows = [email.EmailLead(source="email", search_title="l", search_location="", role_title="A",
                            company="", salary="", location="", contract="", posted="", url="u",
                            job_id="indeed-1", raw_block="")] * 2
    assert len(email.dedupe(list(rows))) == 1


def test_labels_must_name_a_known_provider():
    import pytest
    with pytest.raises(SystemExit):
        email.labels_from({"boards": {"email": {"labels": [{"label": "x", "provider": "monster"}]}}})
    assert email.labels_from({"boards": {"email": {}}}) == email.DEFAULT_LABELS


def test_indeed_match_intro_is_not_read_as_the_first_job():
    body = ("Jobs are based on your preferences, profile and activity on Indeed\n\n"
            "Principal Software Engineer\nProfile 29 Ltd - Remote\n"
            "https://uk.indeed.com/pagead/clk?jk=aa11bb22&from=jobi2a\n")
    leads, template = leads_for("job/discovery/indeed", "indeed", "Principal Software Engineer",
                                "donotreply@match.indeed.com", body)
    assert template == "indeed-match"
    assert (leads[0].role_title, leads[0].company, leads[0].location) == \
        ("Principal Software Engineer", "Profile 29 Ltd", "Remote")


def test_a_posting_url_is_recognised_without_the_mail_it_came_in():
    # The downstream pipeline stores URLs, not leads; this is how a line there is matched
    # back to a job this board reported.
    assert email.job_id_from_url("https://www.linkedin.com/jobs/view/4455188953") == "linkedin-4455188953"
    assert email.job_id_from_url("https://www.totaljobs.com/job/107857792") == "totaljobs-107857792"
    assert email.job_id_from_url("https://www.reed.co.uk/jobs/engineer/57229225") == ""
    assert email.job_id_from_url("not a url") == ""


def test_a_sponsored_indeed_link_keeps_one_id_across_mails():
    # /pagead/clk carries no job id and a per-impression `ad` blob, so hashing the URL would
    # report the same advert as a new job every morning.
    first = email.job_id_for("indeed", "https://uk.indeed.com/pagead/clk?ad=AAA&jrtk=1",
                             "Principal Software Engineer", "Profile 29 Ltd")
    second = email.job_id_for("indeed", "https://uk.indeed.com/pagead/clk?ad=ZZZ&jrtk=2",
                              "Principal Software Engineer", "Profile 29 Ltd")
    assert first == second
    assert first != email.job_id_for("indeed", "https://uk.indeed.com/pagead/clk?ad=AAA",
                                     "Staff Engineer", "Profile 29 Ltd")
    # A link that does carry the job id still uses it, whatever the card said.
    assert email.job_id_for("indeed", "https://uk.indeed.com/rc/clk?jk=abc123",
                            "Anything", "Anyone") == "indeed-abc123"


CTS = "https://cts.indeed.com/v3/"


def cts_link(destination: str) -> str:
    import base64, gzip, json as _json
    blob = base64.urlsafe_b64encode(gzip.compress(_json.dumps({"u": destination}).encode())).decode().rstrip("=")
    return CTS + blob + "/signature-part"


INDEED_ROLE_MATCH = """\
Hi Mike,

Your background in full-stack development could align with this Senior Software Engineer role at Edun Ltd.

Senior Software Engineer
Edun ltd
Newcastle upon Tyne NE1
Salary: £49,000 - £59,000 a year
Job type: Full-time

Benefits:
  - On-site parking

View job: {view}
Apply now: {apply}

Manage email settings: {settings}
"""


def test_indeed_click_wrappers_are_decoded_without_asking_indeed():
    body = INDEED_ROLE_MATCH.format(
        view=cts_link("https://uk.indeed.com/pagead/clk?ad=AAA&jrtk=1"),
        apply=cts_link("https://uk.indeed.com/pagead/clk?ad=ZZZ&jrtk=1"),
        settings=cts_link("https://uk.indeed.com/preferences?from=email"))
    leads, template = leads_for("job/discovery/indeed", "indeed", "Senior Software Engineer @ Edun ltd",
                                "donotreply@match.indeed.com", body)
    assert template == "indeed-role-match"
    assert (leads[0].role_title, leads[0].company, leads[0].location) == \
        ("Senior Software Engineer", "Edun ltd", "Newcastle upon Tyne NE1")
    # The apply link is the same posting, and the settings link is not a posting at all.
    assert len({l.job_id for l in leads}) == 1
    assert all("preferences" not in l.url for l in leads)


def test_an_undecodable_wrapper_is_dropped_rather_than_reported_as_a_job():
    body = INDEED_ROLE_MATCH.format(view=CTS + "not-a-payload", apply=CTS + "also-not",
                                    settings=CTS + "nope")
    leads, _ = leads_for("job/discovery/indeed", "indeed", "x", "donotreply@match.indeed.com", body)
    assert leads == []
