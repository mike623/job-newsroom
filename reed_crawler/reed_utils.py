from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import salary
from board_config import BASE_REED
from lead import Lead

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


def parse_jobs_from_markdown(markdown: str, spec: dict) -> list[Lead]:
    # Reed result items usually start with Markdown H2 link lines.
    pattern = re.compile(r"^## \[([^\]]+)\]\(([^\)]+)\).*?(?=^## \[|\Z)", re.M | re.S)
    jobs: list[Lead] = []
    for match in pattern.finditer(markdown):
        title = match.group(1).strip()
        url = urljoin(BASE_REED, link_target(match.group(2)))
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

        jobs.append(Lead(
            source="reed",
            search_title=spec["title"],
            search_location=spec["location"],
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


def write_report(jobs: list[Lead], out_md: Path) -> None:
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
