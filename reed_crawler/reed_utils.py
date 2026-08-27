from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin

import salary

BASE = "https://www.reed.co.uk"


def slug_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def safe_name(*parts: str) -> str:
    return "__".join(slug_text(p).replace("-", "_") for p in parts)


def reed_search_url(title: str, location: str, proximity: int) -> str:
    return f"{BASE}/jobs/{slug_text(title)}-jobs-in-{slug_text(location)}?proximity={proximity}"


@dataclass
class SearchSpec:
    title: str
    location: str
    proximity: int

    @property
    def url(self) -> str:
        return reed_search_url(self.title, self.location, self.proximity)

    @property
    def name(self) -> str:
        return safe_name(self.title, self.location)


@dataclass
class Job:
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

    def to_dict(self):
        return asdict(self)


def link_target(destination: str) -> str:
    """The href out of a markdown link target.

    Reed renders its cards as `[Title](https://... "Title")`, so the captured target carries
    the quoted title as well. Left on, it rides along in every report, breaks the href the
    dashboard renders, and reaches the downstream pipeline as part of the URL.
    """
    return destination.strip().split(" ", 1)[0].strip("<>")


def extract_job_id(url: str) -> str:
    m = re.search(r"/jobs/[^/]+/(\d+)", url)
    return m.group(1) if m else ""


def parse_jobs_from_markdown(markdown: str, spec: SearchSpec) -> list[Job]:
    # Reed result items usually start with Markdown H2 link lines.
    pattern = re.compile(r"^## \[([^\]]+)\]\(([^\)]+)\).*?(?=^## \[|\Z)", re.M | re.S)
    jobs: list[Job] = []
    for match in pattern.finditer(markdown):
        title = match.group(1).strip()
        url = urljoin(BASE, link_target(match.group(2)))
        block = match.group(0).strip()
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

        posted = company = salary = location = contract = ""
        if len(lines) > 1:
            m = re.search(r"^(.*?) by \[?([^\]\(]+)", lines[1])
            if m:
                posted = m.group(1).strip()
                company = m.group(2).strip()
            else:
                posted = lines[1]

        bullets = [re.sub(r"^\*\s*", "", ln).strip() for ln in lines if ln.startswith("*")]
        if bullets:
            salary = bullets[0] if len(bullets) > 0 else ""
            location = bullets[1] if len(bullets) > 1 else ""
            contract = bullets[2] if len(bullets) > 2 else ""

        # Skip search/category links accidentally matched as jobs.
        if not extract_job_id(url):
            continue

        jobs.append(Job(
            source="reed",
            search_title=spec.title,
            search_location=spec.location,
            role_title=title,
            company=company,
            salary=salary,
            location=location,
            contract=contract,
            posted=posted,
            url=url,
            job_id=extract_job_id(url),
            raw_block=block,
        ))
    return jobs


def dedupe_jobs(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for job in jobs:
        key = job.job_id or "|".join([job.role_title.lower(), job.company.lower(), job.location.lower()])
        if key not in seen:
            seen[key] = job
    return list(seen.values())


def write_report(jobs: list[Job], out_md: Path) -> None:
    lines = ["# Reed job crawl report", "", f"Deduped jobs: {len(jobs)}", "", "## Highest advertised salary", ""]
    for idx, job in enumerate(sorted(jobs, key=salary.sort_key, reverse=True)[:30], 1):
        lines += [
            f"### {idx}. {job.role_title} — {job.company or 'Unknown'}",
            f"- Location: {job.location}",
            f"- Salary: {job.salary}",
            f"- Type: {job.contract}",
            f"- Posted: {job.posted}",
            f"- Search: {job.search_title} / {job.search_location}",
            f"- URL: {job.url}",
            "",
        ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
