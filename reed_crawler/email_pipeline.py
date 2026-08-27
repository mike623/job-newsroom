"""Job leads that arrive as email alerts, rather than from crawling a board.

Every other board here is a search this project runs. This one is the mail the boards already
send: Gmail labels collect the alert digests, and this reads them. Nothing is crawled, so
there is no rate to respect and no markup to defeat — the cost is that a digest states its
jobs in whatever layout the sender shipped this month.

Ported from career-ops/ingest-email-labels.mjs, which wrote a JobSpy CSV. Here the same
extraction writes the ordinary deduped report every other board writes, so the dashboard,
the run record and the cron treat email exactly like Reed.

Three independent axes — never conflate them:
  label    -> which mailbox to read (a filing choice, says nothing about format)
  provider -> URL rules and board name, identified by the SENDER (see PROVIDERS)
  template -> body layout, identified by a body SIGNATURE (see TEMPLATES)
Providers ship several templates and change them without notice. Mail whose template is
unrecognised is named at the end of a run rather than silently yielding subject-derived rows.

Reading needs the `himalaya` CLI configured against the account (`brew install himalaya`).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs

from board_config import load_config, raw_capture_stem, run_stamp
import salary as salary_parser
import run_record
import scan_lock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "email"
RAW = OUT / "raw"
REPORTS = OUT / "reports"

USER_AGENT = "Mozilla/5.0"
HIMALAYA_TIMEOUT = 180
REDIRECT_TIMEOUT = 8
MAX_REDIRECT_HOPS = 5

# A Gmail label is a folder mail is filed into, not a guarantee of format: the same label
# carries several templates, and mail can be filed under the "wrong" one. The label only says
# where to look; the sender says which URL rules apply.
DEFAULT_LABELS = [
    {"label": "job/discovery/indeed", "provider": "indeed"},
    {"label": "job/discovery/linkedin", "provider": "linkedin"},
    {"label": "job/discovery/totaljobs", "provider": "totaljobs"},
    {"label": "job/discovery/jobright", "provider": "jobright"},
]

PROVIDERS = {
    "indeed": {"board": "Indeed", "sender": re.compile(r"@([\w.-]+\.)?indeed\.com$", re.I)},
    "linkedin": {"board": "LinkedIn", "sender": re.compile(r"@([\w.-]+\.)?linkedin\.com$", re.I)},
    "totaljobs": {"board": "Totaljobs",
                  "sender": re.compile(r"@([\w.-]+\.)?(totaljobsmail|totaljobs|cwjobs)\.(com|co\.uk)$", re.I)},
    "jobright": {"board": "Jobright", "sender": re.compile(r"@([\w.-]+\.)?jobright\.ai$", re.I)},
}


@dataclass
class EmailLead:
    source: str
    search_title: str
    search_location: str
    role_title: str
    company: str
    salary: str
    location: str
    contract: str
    posted: str
    url: str
    job_id: str
    raw_block: str
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class MailError(RuntimeError):
    """himalaya could not answer — not configured, not reachable, or no such label."""


def slug(s: str, max_len: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:max_len] or "unknown"


def provider_from_sender(addr: str) -> str:
    addr = str(addr or "")
    return next((name for name, p in PROVIDERS.items() if p["sender"].search(addr)), "")


# --------------------------------------------------------------------------- mailbox

def himalaya(argv: list[str]) -> str:
    try:
        proc = subprocess.run(["himalaya", *argv], capture_output=True, text=True,
                              timeout=HIMALAYA_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise MailError(f"himalaya {' '.join(argv)}: {failure}") from failure
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        raise MailError(f"himalaya {' '.join(argv)} exited {proc.returncode}: {detail[0]}")
    return proc.stdout


def list_envelopes(label: str, limit: int, since: str = "") -> list[dict]:
    """Newest `limit` envelopes in a label, no older than `since` (YYYY-MM-DD).

    Himalaya's search parser rejects `after YYYY-MM-DD and order by date desc`, so the query
    stays simple and the cutoff is applied here. himalaya prints IMAP warnings on stderr and
    sometimes before the JSON, so the payload is found rather than assumed.
    """
    out = himalaya(["-o", "json", "envelope", "list", "-f", label, "-s", str(limit),
                    "order by date desc"])
    start = out.find("[")
    if start < 0:
        raise MailError(f"himalaya returned no JSON for {label}")
    try:
        envelopes = json.loads(out[start:])
    except json.JSONDecodeError as failure:
        raise MailError(f"himalaya returned unreadable JSON for {label}: {failure}") from failure
    if not since:
        return envelopes
    return [e for e in envelopes if str(e.get("date") or "")[:10] >= since]


def read_message(label: str, message_id) -> str:
    return himalaya(["message", "read", "--preview", "-f", label, str(message_id)])


def mark_message_read(label: str, message_id) -> None:
    # Himalaya applies the IMAP "seen" flag when reading without --preview.
    himalaya(["message", "read", "-f", label, str(message_id)])


# --------------------------------------------------------------------------- URLs

URL_RE = re.compile(r"https?://[^\s<>\"')]+")
NOISE_URL = re.compile(
    r"mail\.google|google\.com/url|unsubscribe|preferences|privacy|terms|support\.indeed"
    r"|subscriptions\.indeed|profile\.indeed|account\.indeed|legal\?", re.I)
BODY_NOISE_URL = re.compile(
    r"mail\.google|google\.com/url|unsubscribe|preferences|privacy|terms|support\.indeed"
    r"|subscriptions\.indeed|profile\.indeed|account\.indeed|indeed\.com/jobs\?|legal\?"
    r"|facebook\.com|youtube\.com|x\.com/totaljobs|twitter\.com|totaljobsmail\.com|magiclink", re.I)
SOCIAL_HOSTS = {"facebook.com", "youtube.com", "x.com", "twitter.com"}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def allows_url(provider: str, url: str) -> bool:
    """Whether this URL is a job posting for this provider, rather than a footer or a search."""
    parts = urlsplit(url)
    host = host_of(url)
    if not host or parts.scheme not in ("http", "https"):
        return False
    if NOISE_URL.search(url.lower()):
        return False
    path = parts.path

    if provider == "indeed":
        if not re.search(r"(^|\.)indeed\.com$", host):
            return False
        # cts.indeed.com wraps every link in the match mail, jobs and footer alike; what it
        # wraps is checked once it has been unwrapped.
        if host == "cts.indeed.com":
            return path.startswith("/v3/")
        return bool(re.match(r"^/(?:rc/clk|pagead/clk|viewjob|m/basecamp/viewjob)", path))

    if provider == "totaljobs":
        # Any totaljobsmail.com tracker host (click., jobs., ...) — resolved later.
        if re.search(r"(^|\.)totaljobsmail\.com$", host):
            return True
        if host in SOCIAL_HOSTS or re.match(r"^(help|facebook|youtube|x|twitter)\.", host):
            return False
        # totaljobs.com: a real posting is /job/<id>; /jobs/<keyword> is a search page.
        if re.search(r"(^|\.)totaljobs\.com$", host):
            return bool(re.match(r"^/job/\d", path, re.I))
        return bool(re.search(r"(^|\.)(cwjobs\.co\.uk|jobserve\.com)$", host)
                    and re.search(r"/(job|jobs)\b", path, re.I))

    if provider == "linkedin":
        # Alert mail also links /comm/jobs/alerts and /comm/jobs/search-results — not postings.
        return bool(re.search(r"(^|\.)linkedin\.com$", host)
                    and re.match(r"^/(?:comm/)?jobs/view/\d+", path))

    if provider == "jobright":
        return bool(re.search(r"(^|\.)jobright\.ai$", host) and "/jobs/info/" in path)

    return False


def is_tracker(url: str) -> bool:
    """A click wrapper standing in front of a posting, rather than the posting itself."""
    host = host_of(url)
    return bool(re.search(r"(^|\.)totaljobsmail\.com$", host)
                or (host == "cts.indeed.com" and url.split("?")[0].find("/v3/") > 0))


def unwrap_indeed_cts(url: str) -> str:
    """The destination inside a cts.indeed.com click wrapper, read without asking Indeed.

    Indeed's match mail links every job through `cts.indeed.com/v3/<blob>`, and the blob is a
    base64url gzip of `{"u": "<destination>"}` — the whole redirect is already in the link.
    Decoding it locally keeps those mails from costing a request to a board that blocks
    automated traffic hard; five mails a day were otherwise producing no leads at all.
    """
    blob = url.split("/v3/", 1)[1].split("/")[0].split("?")[0] if "/v3/" in url else ""
    if not blob:
        return url
    try:
        payload = gzip.decompress(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        destination = json.loads(payload).get("u") or ""
    except (ValueError, OSError, json.JSONDecodeError):
        return url
    return destination or url


def unwrap_magic_link(url: str) -> str:
    """Reduce a per-recipient link to the public posting it points at.

    Totaljobs wraps job links as /v2/magiclink/exchange?magicLink=<JWT>&returnUrl=<path>; the
    JWT is an auth token, not part of the job URL. LinkedIn and Jobright append per-email
    tracking to the same posting, which would defeat dedup if kept.
    """
    parts = urlsplit(url)
    host = host_of(url)
    if not host:
        return url

    if re.search(r"(^|\.)jobright\.ai$", host) and parts.path.startswith("/jobs/info/"):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    if re.search(r"(^|\.)linkedin\.com$", host):
        job = re.match(r"^/(?:comm/)?jobs/view/(\d+)", parts.path)
        return f"https://www.linkedin.com/jobs/view/{job.group(1)}" if job else url

    if not re.search(r"(^|\.)totaljobs\.com$", host):
        return url
    if not re.match(r"^/v\d*/?magiclink/exchange$", parts.path, re.I):
        return url
    returned = (parse_qs(parts.query).get("returnUrl") or [""])[0]
    if not returned:
        return url
    destination = urlsplit(urljoin(f"{parts.scheme}://{parts.netloc}", returned))
    path = destination.path
    # The apply button and the title link point at one posting: /job/<id>/application/redirection
    # and /job/<id>. Collapse to the posting so dedup sees one row.
    job = re.match(r"^/job/(\d+)", path, re.I)
    if job:
        path = f"/job/{job.group(1)}"
    return urlunsplit((destination.scheme, destination.netloc, path, "", ""))


_OPENER = None


def _location(url: str) -> str:
    """One hop: the Location header, without fetching the destination.

    Fetching it hits Cloudflare (405 on HEAD, 403 or a timeout on GET), so only the redirect
    is read.
    """
    global _OPENER
    if _OPENER is None:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        _OPENER = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _OPENER.open(request, timeout=REDIRECT_TIMEOUT) as reply:
            return reply.headers.get("Location") or ""
    except urllib.error.HTTPError as reply:
        return reply.headers.get("Location") or ""
    except (urllib.error.URLError, OSError):
        return ""


def resolve_redirect(url: str) -> str:
    current = url
    for _ in range(MAX_REDIRECT_HOPS):
        if not is_tracker(current):
            break
        if host_of(current) == "cts.indeed.com":
            unwrapped = unwrap_indeed_cts(current)
            if unwrapped == current:
                break
            current = unwrapped
            continue
        nxt = _location(current).strip()
        if not nxt:
            break
        nxt = urljoin(current, nxt)
        if nxt == current:
            break
        current = nxt
    return unwrap_magic_link(current)


def normalise_raw_url(s: str) -> str:
    return re.sub(r"[),.;]+$", "", str(s).replace("&amp;", "&"))


def extract_urls(provider: str, text: str, max_urls: int) -> list[tuple[str, str]]:
    """[(raw, url)] — raw as it appears in the body, url the canonical posting.

    The raw form is kept because it is what lines a URL up with the text around it.
    """
    by_url: dict[str, str] = {}
    candidates = 0
    for match in URL_RE.finditer(text):
        # Cap accepted rows, not candidates: a totaljobs mail wraps footer and nav junk in the
        # same tracker host as the jobs, so counting candidates starves the real links.
        if len(by_url) >= max_urls or candidates >= max_urls * 8:
            break
        raw = normalise_raw_url(match.group(0))
        if not allows_url(provider, raw):
            continue
        candidates += 1
        resolved = resolve_redirect(raw)
        if is_tracker(raw):
            if resolved == raw or not allows_url(provider, resolved):
                continue
        elif not allows_url(provider, resolved) and not allows_url(provider, raw):
            continue
        by_url.setdefault(resolved, raw)
    return [(raw, url) for url, raw in by_url.items()]


def job_id_for(provider: str, url: str, title: str = "", company: str = "") -> str:
    """A stable id for a posting, provider-scoped so two boards cannot collide.

    Providers that put the posting's id in the URL give a real one. Indeed's *sponsored* links
    do not: `/pagead/clk` carries no job id and a per-impression `ad` blob, so the same advert
    arrives as a different URL every morning and hashing the URL would report it as a new job
    each day. Where the URL is not an identity, what the card said is — hence the fallback to
    title and company, which is what the mail actually promises to repeat.
    """
    parts = urlsplit(url)
    patterns = {
        "linkedin": r"/jobs/view/(\d+)",
        "totaljobs": r"/job/(\d+)",
        "jobright": r"/jobs/info/([\w-]+)",
    }
    found = re.search(patterns[provider], parts.path) if provider in patterns else None
    if found:
        return f"{provider}-{found.group(1)}"
    if provider == "indeed":
        jk = (parse_qs(parts.query).get("jk") or [""])[0]
        if jk:
            return f"indeed-{jk}"
    identity = f"{title.lower()}|{company.lower()}" if (title or company) else url
    return f"{provider or 'email'}-{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:12]}"


def provider_from_url(url: str) -> str:
    """Which provider's posting this URL is, or "" if it is not a posting at all.

    The same rules that decide whether to keep a link out of a mail decide, later, whether a
    line in the downstream pipeline refers to a lead this board produced.
    """
    return next((name for name in PROVIDERS if allows_url(name, url)), "")


def job_id_from_url(url: str) -> str:
    """The id this board would give that URL, so a job can be recognised from the URL alone."""
    provider = provider_from_url(url)
    return job_id_for(provider, url) if provider else ""


# --------------------------------------------------------------------------- bodies

def clean_text(s: str) -> str:
    s = str(s or "").replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def strip_noisy_urls(s: str) -> str:
    return URL_RE.sub(lambda m: "" if BODY_NOISE_URL.search(m.group(0)) else m.group(0), str(s or ""))


# Every provider sends digests: the subject names one job, the body lists 6-25. Without
# per-job parsing every row inherits the subject and nothing downstream can tell them apart.
# Each mail is a run of text blocks delimited by links; a template says where in a block the
# title, company and location sit relative to its job link.
BLOCK_NOISE = re.compile(
    r"^([-=_*~]{3,}$|<#|apply now|top applicant|apply with resume|actively recruiting|easy apply"
    r"|easily apply|responsive employer|be an early applicant|new\b|just posted"
    r"|posted (today|yesterday)|\d+\+? (minute|hour|day|week|month)s? ago|more$|strong fit"
    r"|good fit|your job alert|see all jobs|see matching results|view job|explore this|check out"
    r"|hi |we found these|jobs \d+-\d+|\d+\+ referrals|\d+% match|\d+%$|indeed job alert"
    # Indeed's match mail opens with this line; left in, it becomes the first job's title and
    # shunts every other field of that card along by one.
    r"|jobs are based on your preferences"
    r"|from:|to:|subject:|date:|\d+ (new )?jobs?\b)", re.I)

TOTALJOBS_TERMS = re.compile(
    r"^(permanent|contract|temporary|full[- ]?time|part[- ]?time|freelance|apprenticeship"
    r"|from [£$€]|up to [£$€]|[£$€]|competitive|(starting |basic )?salary\b)", re.I)


def at(rows: list[str], index: int) -> str:
    try:
        return rows[index] or ""
    except IndexError:
        return ""


def indeed_listing(before, after, prefix) -> dict:
    # Title / "Company - Location" / salary / ... / <url>
    company, _, location = at(before, 1).partition(" - ")
    return {"title": at(before, 0), "company": company, "location": location}


def linkedin_listing(before, after, prefix) -> dict:
    # Title / Company / Location / ... / "View job: <url>"
    return {"title": at(before, 0), "company": at(before, 1), "location": at(before, 2)}


# "Salary: £49,000 - £59,000 a year" / "Job type: Full-time" — the first stated term is what
# marks the end of a card's identity in Indeed's single-role mail.
INDEED_TERMS = re.compile(r"^(salary|job type|work setting|pay|shift and schedule):", re.I)


def indeed_role_listing(before, after, prefix) -> dict:
    # ...intro sentence / Title / Company / Location / "Salary: ..." / "Job type: ..." / link
    marked = next((i for i, line in enumerate(before) if INDEED_TERMS.match(line)), len(before))
    card = before[max(0, marked - 3):marked]
    return {"title": at(card, 0), "company": at(card, 1), "location": at(card, 2)}


def totaljobs_digest(before, after, prefix) -> dict:
    # Title / <url> / Company / Location / contract / salary
    return {"title": at(before, -1), "company": at(after, 0), "location": at(after, 1)}


def totaljobs_recommendation(before, after, prefix) -> dict:
    # ...intro / Title / Company / Location / contract / salary / "Apply Now" / <url> / JD text
    tail = [line for line in before if not TOTALJOBS_TERMS.match(line)][-3:]
    return {"title": at(tail, 0), "company": at(tail, 1), "location": at(tail, 2)}


def jobright_alert(before, after, prefix) -> dict:
    # Company / "industry - stage" / NN% / "<title> (<url>)" / [salary] / Location.
    # Digest variants omit the inline title — it exists only in the HTML part.
    title = re.sub(r"\s*\|.*$", "", str(prefix or ""))
    title = re.sub(r"\bjob details\b", "", title, flags=re.I).strip()
    return {
        "title": title,
        "company": at(before, 0),
        "location": next((line for line in after if not re.match(r"^[£$€]", line)), ""),
    }


# One template = one body layout, identified by a signature and read by an adaptor. Providers
# ship several and change them without notice, so this matches on what the body says, not on
# which label the mail arrived under. First matching signature wins; order matters only where
# two signatures could both fire (specific before generic).
TEMPLATES = [
    {"id": "linkedin-job-alert", "provider": "linkedin",
     "signature": re.compile(r"^\s*View job:", re.I | re.M), "adaptor": linkedin_listing},
    # One role, addressed personally: "...could align with this Senior Software Engineer role
    # at Edun Ltd". Listed before the generic match mail, whose wording it otherwise matches.
    {"id": "indeed-role-match", "provider": "indeed",
     "signature": re.compile(r"could (?:align with|be an? [a-z ]*match for) this .{0,80}? role at", re.I),
     "adaptor": indeed_role_listing},
    {"id": "indeed-job-alert", "provider": "indeed",
     "signature": re.compile(r"Indeed Job Alert", re.I), "adaptor": indeed_listing},
    # donotreply@match.indeed.com — different mail, same listing layout as the alert.
    {"id": "indeed-match", "provider": "indeed",
     "signature": re.compile(r"could be a match for this"
                             r"|based on your preferences, profile and activity on Indeed", re.I),
     "adaptor": indeed_listing},
    {"id": "totaljobs-search-digest", "provider": "totaljobs",
     "signature": re.compile(r"new jobs that match your search|Check out your latest matches"
                             r"|Picked for you", re.I),
     "adaptor": totaljobs_digest},
    {"id": "totaljobs-recommendation", "provider": "totaljobs",
     "signature": re.compile(r"We recommend this job for you", re.I),
     "adaptor": totaljobs_recommendation},
    {"id": "jobright-alert", "provider": "jobright",
     "signature": re.compile(r"Jobright Instant Alert|curated to align with your preferences", re.I),
     "adaptor": jobright_alert},
]


def detect_template(provider: str, body: str) -> dict | None:
    return next((t for t in TEMPLATES
                 if t["provider"] == provider and t["signature"].search(str(body or ""))), None)


def job_meta_from_body(template: dict | None, body: str, raw_to_url: dict[str, str]) -> dict[str, dict]:
    """Per-posting title, company and location, read off the block each job link sits in."""
    if not template or not raw_to_url:
        return {}

    blocks: list[dict] = [{"lines": [], "urls": [], "prefix": ""}]
    for raw_line in str(body or "").split("\n"):
        # Totaljobs writes "Apply Now" with exotic spaces; normalise or the noise filter misses it.
        line = re.sub(r"[   ​]", " ", raw_line)
        links = list(URL_RE.finditer(line))
        if links:
            # Any link ends the block, not only a known job link: header and footer links sit
            # between the digest intro and the first posting and must not bleed into it.
            blocks.append({
                "lines": [],
                "urls": [u for u in (raw_to_url.get(normalise_raw_url(m.group(0))) for m in links) if u],
                # Jobright puts the job title inline, ahead of the link on the same line.
                "prefix": line[:links[0].start()].strip(),
            })
        elif line.strip() and not BLOCK_NOISE.match(line.strip()):
            blocks[-1]["lines"].append(line.strip())

    by_url: dict[str, dict] = {}
    previous: dict = {}
    for i, block in enumerate(blocks):
        before = blocks[i - 1]["lines"] if i else []
        info = template["adaptor"](before, block["lines"], block["prefix"]) or {}
        if not info.get("title") and not info.get("company"):
            # "View job: <url>" then "Apply now: <url>" on the next line, with nothing said in
            # between, is one posting linked twice. Indeed writes both, under different
            # per-click URLs, and left unattributed the second becomes a second job.
            info = previous if (i and blocks[i - 1]["urls"] and not before) else {}
        if not info:
            continue
        for url in block["urls"]:
            by_url.setdefault(url, {"title": info.get("title") or "",
                                    "company": info.get("company") or "",
                                    "location": info.get("location") or ""})
        if block["urls"]:
            previous = info
    return by_url


# "Magnify just posted a 97% match Senior Software Engineer role 24 minutes ago" (Jobright)
JOBRIGHT_POSTED = re.compile(r"^(.+?) just posted an? \d+% match (.+?) role\b", re.I)
# "Senior Full-Stack Engineer (Backend + Frontend) at Soda: up to €120K/year" (LinkedIn)
LINKEDIN_ALERT = re.compile(r"^(.+)\s+at\s+([^:]+?)(?::\s*up to .*)?$", re.I)


def title_from_subject(subject: str, provider: str = "") -> str:
    s = subject or ""
    posted = JOBRIGHT_POSTED.match(s)
    if posted:
        return posted.group(2).strip()
    if provider == "linkedin":
        alert = LINKEDIN_ALERT.match(s)
        if alert:
            return alert.group(1).strip()
    for pattern in (r"(?:recommendation|matches?)\s*:\s*([^—\-+|]+)",
                    r"is hiring for\s+([^+—\-]+)",
                    r"[\"“]([^\"”]*(?:software|engineer|developer|lead|architect)[^\"”]*)[\"”]"):
        found = re.search(pattern, s, re.I)
        if found:
            return found.group(1).strip()
    return "Job lead (email)"


def company_from_subject(subject: str, provider: str = "") -> str:
    """Only trustworthy when the mail is about one posting.

    A digest subject names one company but carries many jobs, and mistagging the employer
    poisons dedup downstream.
    """
    s = subject or ""
    posted = JOBRIGHT_POSTED.match(s)
    if posted:
        return posted.group(1).strip()
    if provider == "linkedin":
        alert = LINKEDIN_ALERT.match(s)
        if alert:
            return alert.group(2).strip()
    hiring = re.match(r"^(.+?)\s+is hiring for\b", s, re.I)
    return hiring.group(1).strip() if hiring else ""


def location_from_subject(subject: str) -> str:
    s = subject or ""
    tail = re.search(r"\bin\s+([^|—]+)$", s, re.I)
    if tail:
        return re.sub(r"\s+and\s+\d+\s+more.*$", "", tail.group(1), flags=re.I).strip()
    return "Remote UK" if re.search(r"remote.*uk|uk.*remote", s, re.I) else ""


def leads_from_message(meta: dict, envelope: dict, body: str, max_urls: int) -> tuple[list[EmailLead], str]:
    """One mail's leads, plus the id of the template that read it ("" when unrecognised)."""
    sender = (envelope.get("from") or {}).get("addr") or ""
    # Sender wins over the label: mail gets filed by hand and lands in the wrong folder.
    provider = provider_from_sender(sender) or meta["provider"]
    board = (PROVIDERS.get(provider) or {}).get("board", provider.title())
    template = detect_template(provider, body)

    found = extract_urls(provider, body, max_urls)
    subject = envelope.get("subject") or ""
    posted = str(envelope.get("date") or "")[:10] or date.today().isoformat()
    context = clean_text("\n".join([
        f"Source Gmail label: {meta['label']}.",
        f"Source job board: {board}.",
        f"Source email id: {envelope.get('id')}.",
        f"Source sender: {sender}.",
        f"Source template: {template['id'] if template else 'unrecognized'}.",
        f"Source subject: {subject}.",
        "",
        clean_text(strip_noisy_urls(body))[:1200],
    ]))

    # A digest subject names one of its jobs; attributing it to all of them invents data.
    single = len(found) == 1
    subject_company = company_from_subject(subject, provider) if single else ""
    subject_title = title_from_subject(subject, provider)
    subject_location = location_from_subject(subject)
    per_job = job_meta_from_body(template, body, {raw: url for raw, url in found})

    leads = []
    for _, url in found:
        job = per_job.get(url, {})
        role_title = job.get("title") or (subject_title if single else "Job lead (email)")
        company_name = job.get("company") or subject_company or board
        leads.append(EmailLead(
            source="email",
            search_title=meta["label"],
            search_location="",
            role_title=role_title,
            company=company_name,
            salary="",                      # alert mail states pay inconsistently; the posting has it
            location=job.get("location") or subject_location,
            contract="",
            posted=posted,
            url=url,
            job_id=job_id_for(provider, url, role_title, company_name),
            raw_block=context,
        ))
    return leads, (template["id"] if template else "")


def dedupe(leads: list[EmailLead]) -> list[EmailLead]:
    seen: dict[str, EmailLead] = {}
    for lead in leads:
        seen.setdefault(lead.job_id, lead)
    return list(seen.values())


def labels_from(cfg: dict) -> list[dict]:
    board = (cfg.get("boards") or {}).get("email") or {}
    rows = []
    for item in board.get("labels") or DEFAULT_LABELS:
        label = str(item.get("label") or "").strip()
        provider = str(item.get("provider") or "").strip()
        if not label or provider not in PROVIDERS:
            raise SystemExit(f"boards.email.labels: bad entry {item!r}; provider must be one of "
                             f"{', '.join(PROVIDERS)}")
        rows.append({"label": label, "provider": provider})
    return rows


def scan(cfg: dict, limit: int | None = None, allow_disabled: bool = False,
         mark_read: bool | None = None, since: str = "") -> Path:
    board = (cfg.get("boards") or {}).get("email") or {}
    if not board.get("enabled") and not allow_disabled:
        raise SystemExit("Email is disabled in config.yml. Use --allow-disabled for manual smoke tests.")

    labels = labels_from(cfg)
    per_label = int(limit or board.get("messages_per_label", 25))
    max_urls = int(board.get("max_urls_per_message", 12))
    if mark_read is None:
        mark_read = bool(board.get("mark_read", False))
    if not since:
        days = int(board.get("max_age_days", 14))
        since = (datetime.now() - timedelta(days=days)).date().isoformat()

    RAW.mkdir(parents=True, exist_ok=True)
    with scan_lock.hold("email"):
        stamp = run_stamp()
        with run_record.record("email", stamp) as findings:
            all_leads: list[EmailLead] = []
            failures: list[str] = []
            templates: dict[str, int] = {}
            unrecognized: list[str] = []

            for meta in labels:
                print(f"Reading {meta['label']} (since {since}, newest {per_label})")
                try:
                    envelopes = list_envelopes(meta["label"], per_label, since)
                except MailError as failure:
                    print(f"  failed: {failure}")
                    failures.append(meta["label"])
                    continue

                captured, produced = [], 0
                for envelope in envelopes:
                    try:
                        body = read_message(meta["label"], envelope.get("id"))
                    except MailError as failure:
                        print(f"  message {envelope.get('id')}: {failure}")
                        continue
                    leads, template = leads_from_message(meta, envelope, body, max_urls)
                    key = template or f"{meta['provider']}:unrecognized"
                    templates[key] = templates.get(key, 0) + 1
                    # A silent parse failure looks identical to a quiet inbox — name the mail so
                    # a new template shows up as work to do rather than as missing jobs.
                    if not template and leads:
                        unrecognized.append(f"{meta['label']}#{envelope.get('id')} "
                                            f"{str(envelope.get('subject') or '')[:110]}")
                    captured.append({"envelope": envelope, "body": body, "template": template,
                                     "leads": len(leads)})
                    all_leads.extend(leads)
                    produced += len(leads)
                    if mark_read and leads:
                        try:
                            mark_message_read(meta["label"], envelope.get("id"))
                        except MailError as failure:
                            print(f"  could not mark {envelope.get('id')} read: {failure}")

                stem = raw_capture_stem(slug(meta["label"]), stamp)
                (RAW / f"{stem}.json").write_text(json.dumps(captured, indent=2), encoding="utf-8")
                print(f"  messages={len(envelopes)} leads={produced}")

            # scan_health classifies a crawled page body, which this board has none of. Here an
            # empty label is the normal state of a read inbox, and the real failure is himalaya
            # being unable to answer at all.
            if labels and len(failures) == len(labels):
                raise SystemExit(f"email: no label could be read ({', '.join(failures)}). "
                                 "Check that himalaya is installed and configured.")

            deduped = sorted(dedupe(all_leads), key=salary_parser.sort_key, reverse=True)
            REPORTS.mkdir(parents=True, exist_ok=True)
            raw_path = REPORTS / f"email_raw_{stamp}.json"
            dedup_path = REPORTS / f"email_deduped_{stamp}.json"
            raw_path.write_text(json.dumps([x.to_dict() for x in all_leads], indent=2), encoding="utf-8")
            dedup_path.write_text(json.dumps([x.to_dict() for x in deduped], indent=2), encoding="utf-8")

            print(f"Email raw={len(all_leads)} deduped={len(deduped)}")
            print("templates: " + (" ".join(f"{k}={v}" for k, v in templates.items()) or "none"))
            for line in unrecognized:
                print(f"  unrecognized template: {line}")
            if failures:
                print(f"labels that could not be read: {', '.join(failures)}")
            findings.update(jobs=len(deduped), searches=len(labels))
            print(f"Deduped JSON: {dedup_path}")
            return dedup_path


def main() -> None:
    ap = argparse.ArgumentParser(description="collect job leads from labelled alert email")
    ap.add_argument("command", choices=["scan"])
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, help="newest N messages per label")
    ap.add_argument("--since", default="", help="YYYY-MM-DD; defaults to boards.email.max_age_days")
    ap.add_argument("--mark-read", action="store_true", help="flag mail that produced leads as seen")
    ap.add_argument("--allow-disabled", action="store_true",
                    help="manual smoke test even when boards.email.enabled=false")
    args = ap.parse_args()
    scan(load_config(ROOT / args.config), args.limit, args.allow_disabled,
         mark_read=True if args.mark_read else None, since=args.since)


if __name__ == "__main__":
    main()
