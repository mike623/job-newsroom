# 1. No cap on the number of searches

Date: 2026-09-14

## Status

Accepted

## Context

Every board built its searches as the product of its configured titles and locations, then
truncated that list to `max_pages_per_run` before scanning. The cap existed to bound how many
requests a scan made against one host, because job boards block scrapers that move quickly.

Truncating a product silently starves whole axes. The list was built title-outer, so the first
N rows were all one title, and a cap below the product meant later titles were never asked for
at all. Measured against the live config:

- **Reed** — 5 titles x 4 locations, capped at 8. It asked for 2 titles. `principal engineer`,
  `lead software engineer` and `full stack engineer` had not been searched on Reed for months.
- **Talent** — 4 explicit `search_params` rows, capped at 2. Rows 3 and 4 never ran.

A first fix, `paired()`, reordered the product cyclically and rotated the pairing by the day, so
a truncated list still covered every title and deferred combinations rather than skipping them.
It worked, and it only reached one of the three branches of `build_board_urls` — the
`search_params` branch and the feed branch still truncated raw, so Talent stayed starved.

That is the shape of the problem: the cap was a safety control that had to be made fair in
every branch that produced searches, and getting it wrong was invisible. A board that has
quietly stopped asking for three of its five titles looks exactly like a board where those
titles have no jobs.

## Decision

There is no cap on the number of searches. Every board asks for everything its config names,
every run.

What bounds a scan is the request rate and, on a board that pages within one search, the page
budget:

- `delay_seconds` — the jittered wait between requests, per board.
- `pages_per_search` / `pages_per_query` — how many pages one search may fetch.

`max_pages_per_run` is removed from both config files and from all three branches of
`build_board_urls`. `paired()` and `JOB_CRAWLER_ROTATION` are deleted with it: they existed only
to make truncation fair, and there is no truncation left to be fair about. The product is built
by a plain nested loop again.

`--limit N` stays. It truncates specs for manual smoke tests, where covering every title does
not matter.

## Consequences

Request volume rises, and not evenly. Measured against the live config:

| board | requests before | requests after |
| --- | --- | --- |
| reed | 8 | 20 |
| totaljobs | 5 | 20 |
| indeed | 5 | 20 |
| adzuna | 20 | 20 |
| linkedin | 10 | 20 |
| talent | 2 | 4 |
| themuse | 12 | 12 |
| devitjobs, remoteok | 2 | 2 |

64 to 118 requests for a full scan — 4x on totaljobs and indeed. Boards are separate hosts and
run concurrently, so wall clock is bounded by the slowest board rather than the total: roughly
10 minutes to 20.

LinkedIn absorbs its increase by paging less. It is the one host that has demonstrably blocked
us (429 and 999 are documented, and the response is to abandon the search rather than retry), so
`pages_per_search` drops from 2 to 1 — 20 specs of one page instead of 5 specs of two. Same
request count as the other crawled boards, twice what it made before, and every title covered.

Adzuna is unchanged: it was already configured to take the full product, being an API with
credentials rather than a crawl.

**Before reintroducing a cap**, read the starvation above. A cap on searches is not a rate
limit — it does not change how often a host is asked, only which questions never get asked at
all. If a scan needs to be smaller, lower the page budget or raise the delay.
