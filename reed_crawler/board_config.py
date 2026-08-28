from __future__ import annotations

import os
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE_REED = "https://www.reed.co.uk"
BASE_TOTALJOBS = "https://www.totaljobs.com"
BASE_INDEED = "https://uk.indeed.com"
BASE_TALENT = "https://uk.talent.com"
BASE_HAYSTACK = "https://haystack.cv"
# Adzuna's own search pages answer any automated fetch with a CloudFront 403, so this board
# is read from Adzuna's JSON API. Credentials are attached by the pipeline at request time,
# never here — these URLs are printed, captured and logged.
BASE_ADZUNA = "https://api.adzuna.com/v1/api/jobs/gb/search"
# LinkedIn publishes no job-search API — Talent Solutions is partner-gated — but its guest
# endpoint answers an unauthenticated GET with a plain HTML fragment of result cards. No
# auth, no JavaScript, no browser: linkedin_pipeline fetches this the way adzuna_pipeline
# fetches its API. The endpoint pages ten cards at a time via `start`.
BASE_LINKEDIN = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def slug_text(s: str) -> str:
    return "-".join(str(s).lower().replace("/", " ").split())


def load_config(path: str | Path = ROOT / "config.yml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def set_board_enabled(board: str, enabled: bool, path: str | Path = ROOT / "config.yml") -> bool:
    """Switch one board on or off in config.yml, leaving the rest of the file alone.

    config.yml is written by hand and most of it is comments explaining why a number is what it
    is. Rewriting it from parsed YAML would throw all of that away, so this edits the single
    line and nothing else.

    The result is parsed before it is kept, and compared against the config that was expected —
    an edit that changed anything but this one value is a corrupted config file, and this is the
    only input the whole project has. Returns True when the file was changed.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text) or {}
    if board not in (before.get("boards") or {}):
        raise ValueError(f"no such board in {path.name}: {board}")
    if bool((before["boards"][board] or {}).get("enabled")) == enabled:
        return False

    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(rf"^  {re.escape(board)}:\s*(#.*)?$", line)), None)
    if start is None:
        raise ValueError(f"could not find the {board} block in {path.name}")
    # The board's own keys are indented four spaces; anything deeper belongs to a sub-section
    # (a board's `full_jd` block has an `enabled` of its own), and anything shallower has ended it.
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^ {0,2}\S", lines[i])), len(lines))

    value = "true" if enabled else "false"
    for i in range(start + 1, end):
        found = re.match(r"^(    enabled:\s*)(true|false)(\s*(?:#.*)?)$", lines[i].rstrip("\n"))
        if found:
            lines[i] = f"{found.group(1)}{value}{found.group(3)}\n"
            break
    else:
        lines.insert(start + 1, f"    enabled: {value}\n")

    updated = "".join(lines)
    expected = {**before, "boards": {**before["boards"],
                                     board: {**(before["boards"][board] or {}), "enabled": enabled}}}
    if (yaml.safe_load(updated) or {}) != expected:
        raise ValueError(f"editing {board} would have changed more of {path.name} than its enabled flag")

    # Written beside the original and moved into place, so an interrupted write cannot leave the
    # project's only input half-finished.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, path)
    return True


def run_stamp() -> str:
    """One timestamp per scan run, shared by that run's raw captures, reports and record.

    A caller that needs the identity up front — the dashboard, which redirects to the run's
    page before the scan has started — passes it down instead.
    """
    return os.environ.get("JOB_CRAWLER_RUN_STAMP") or datetime.now().strftime("%Y-%m-%d_%H%M%S")


def raw_capture_stem(name: str, stamp: str) -> str:
    """Filename stem for a raw capture.

    The stamp is what stops a scan overwriting the previous scan's capture of the same search,
    and it ties a capture back to the report written by the same run.
    """
    return f"{name}__{stamp}"


def values_from_groups(search_cfg: dict, kind: str, groups: list[str]) -> list[str]:
    values = []
    grouped = search_cfg.get(kind, {}) or {}
    for group in groups:
        for item in grouped.get(group, []) or []:
            if item not in values:
                values.append(item)
    return values


def board_titles(cfg: dict, board: str) -> list[str]:
    board_cfg = cfg["boards"][board]
    return values_from_groups(cfg.get("search", {}), "titles", board_cfg.get("title_groups", []))


def board_locations(cfg: dict, board: str) -> list[str]:
    board_cfg = cfg["boards"][board]
    return values_from_groups(cfg.get("search", {}), "locations", board_cfg.get("location_groups", []))


def reed_search_url(title: str, location: str, proximity: int) -> str:
    return f"{BASE_REED}/jobs/{slug_text(title)}-jobs-in-{slug_text(location)}?proximity={proximity}"


def totaljobs_search_url(title: str, location: str, radius: int) -> str:
    slug = slug_text(title).replace(".", "")
    return f"{BASE_TOTALJOBS}/jobs/{slug}/in-{slug_text(location)}?radius={radius}&q={quote_plus(title)}"


def indeed_search_url(title: str, location: str, radius: int) -> str:
    return f"{BASE_INDEED}/jobs?q={quote_plus(title)}&l={quote_plus(location)}&radius={radius}"


def adzuna_search_url(title: str, location: str, distance: int, results_per_page: int = 50, page: int = 1) -> str:
    params = urlencode({
        "what": title,
        "where": location,
        "distance": distance,          # kilometres from `where`
        "results_per_page": results_per_page,
        "content-type": "application/json",
    })
    return f"{BASE_ADZUNA}/{page}?{params}"


def linkedin_search_url(title: str, location: str, distance: int, max_age_days: int = 0, start: int = 0) -> str:
    """One page of LinkedIn guest search results.

    `f_TPR=r<seconds>` is the freshness filter, and it is not optional: an unfiltered first
    page was observed carrying adverts over eight months old alongside today's.
    """
    params = {
        "keywords": title,
        "location": location,
        "distance": distance,          # miles from `location`
        "pageNum": 0,
        "start": start,
    }
    if max_age_days:
        params["f_TPR"] = f"r{int(max_age_days) * 86400}"
    return f"{BASE_LINKEDIN}?{urlencode(params)}"


def talent_search_url(keyword: str, location: str, result_id: str = "") -> str:
    url = f"{BASE_TALENT}/jobs?k={quote_plus(keyword)}&l={quote_plus(location)}"
    if result_id:
        url += f"&id={quote_plus(result_id)}"
    return url


def haystack_search_url(title: str, location: str) -> str:
    # Haystack has no radius/proximity control; the free-text location is the only filter.
    return f"{BASE_HAYSTACK}/jobs?q={quote_plus(title)}&location={quote_plus(location)}"


def build_board_urls(cfg: dict, board: str) -> list[dict]:
    board_cfg = cfg["boards"][board]
    if not board_cfg.get("enabled", False):
        return []
    if board == "email":
        # Not a search: this board reads labelled alert mail, so it has no URLs to build.
        # See reed_crawler/email_pipeline.py.
        return []
    if board_cfg.get("search_params"):
        rows = []
        for item in board_cfg.get("search_params") or []:
            keyword = item.get("k") or item.get("keyword") or item.get("title") or ""
            location = item.get("l") or item.get("location") or ""
            result_id = str(item.get("id") or "")
            if not keyword or not location:
                continue
            if board == "talent":
                url = talent_search_url(keyword, location, result_id)
            else:
                raise ValueError(f"Custom search_params are not supported for board: {board}")
            rows.append({"board": board, "title": keyword, "location": location, "url": url})
        max_pages = board_cfg.get("max_pages_per_run")
        return rows[: int(max_pages)] if max_pages else rows

    titles = board_titles(cfg, board)
    locations = board_locations(cfg, board)
    rows = []
    for title in titles:
        for location in locations:
            if board == "reed":
                url = reed_search_url(title, location, int(board_cfg.get("proximity", 50)))
            elif board == "totaljobs":
                url = totaljobs_search_url(title, location, int(board_cfg.get("radius", 30)))
            elif board == "indeed":
                url = indeed_search_url(title, location, int(board_cfg.get("radius", 50)))
            elif board == "talent":
                url = talent_search_url(title, location)
            elif board == "haystack":
                url = haystack_search_url(title, location)
            elif board == "linkedin":
                url = linkedin_search_url(title, location, int(board_cfg.get("distance", 30)),
                                          int(board_cfg.get("max_age_days", 0)))
            elif board == "adzuna":
                url = adzuna_search_url(title, location, int(board_cfg.get("distance", 30)),
                                        int(board_cfg.get("results_per_page", 50)))
            else:
                raise ValueError(f"Unsupported board: {board}")
            rows.append({"board": board, "title": title, "location": location, "url": url})
    max_pages = board_cfg.get("max_pages_per_run")
    return rows[: int(max_pages)] if max_pages else rows


def jittered(seconds: float, spread: float = 0.35) -> float:
    """A delay near `seconds`, varied by up to `spread` either way.

    A perfectly regular cadence is itself a bot signal: humans are irregular. The average rate
    is unchanged, so this costs nothing against the throttling the delays exist to respect.
    """
    if seconds <= 0:
        return 0.0
    return seconds * random.uniform(1 - spread, 1 + spread)


if __name__ == "__main__":
    cfg = load_config()
    for board in cfg.get("boards", {}):
        rows = build_board_urls(cfg, board)
        print(f"{board}: {len(rows)} urls")
        for row in rows[:5]:
            print(f"  - {row['title']} / {row['location']}: {row['url']}")
