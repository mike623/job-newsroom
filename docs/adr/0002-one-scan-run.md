# 2. One scan run, and boards yield their fetches

Date: 2026-09-14

## Status

Accepted

## Context

Each board module was written by copying the last one. That is a reasonable way to add a board
whose markup is unlike every other board's, and it is how `reed_utils.py`, `totaljobs_pipeline.py`,
`talent_pipeline.py`, `indeed_pipeline.py`, `adzuna_pipeline.py`, `haystack_pipeline.py`,
`linkedin_pipeline.py`, `email_pipeline.py` and `aggregator_pipeline.py` came to exist.

What got copied with the parsing was the scan itself. Every `scan()` ended the same thirty
lines — take the board's lock, stamp the run, open a run record, apply the salary parser,
dedupe, sort by pay, write the raw and deduped reports, update the record, let `scan_health`
decide whether the run failed — and began with the same loop: for each search, fetch, write a
stamped capture, classify the page, parse it, sleep.

Those lines are where the invariants live. One scan per board. Raw captures carry the run
stamp. An empty page body is a failure, not zero results. Per-host request rate is the safety
property. Report filenames are a contract. Nine copies meant nine places for each of those to
be right, and nothing that could check them: the only test guarding the stamped-capture rule
read the pipeline files as *text* and asserted that a local variable was named `stem`.

The measurements that decided it:

- The record type was declared nine times, field for field identical.
- `slug` was byte-identical in eight of nine; `dedupe` in seven of nine.
- The enabled check existed in three shapes, and `run_reed_scan.py` had none at all — it
  scanned a board that `config.yml` said was disabled.
- `aggregator_pipeline.py`, added the same week this was written, was the ninth copy. Its own
  docstring argues that copying identical fetching per feed would make "a fix to health
  classification or capture naming an edit per feed, and the one that gets missed is a board
  that quietly stops honouring an invariant." That argument, one level up, is this ADR.

## Decision

Two modules, and boards keep their parsing.

**`scan_run.py`** owns a run: the lock, the stamp, the run record, the salary parser, dedupe,
the sort, both report writes, the findings, and the verdict. Every board uses it, including
`email_pipeline.py`, which has no searches at all.

**`scan_search.py`** owns driving a board's searches: the spec loop, stamped captures, the
health tally, the delay between requests, and the per-search request budget. The eight boards
that search use it.

**A board yields its fetches.** `fetches(spec)` is an async generator handing back a `Fetched` —
the response, the leads parsed from it, the files worth keeping, and a suffix when one search
costs several requests. The board owns its own inner loop, because that is what differs:
LinkedIn pages by the number of cards that arrived and abandons a search on a block, Haystack
retries once when its search backend fails, the feeds page until the feed says stop.

The direction matters and is the reason for the generator. A generator is lazy, so the next
request cannot happen until the head has classified the last one, counted it, and waited. The
rate and the budget are therefore held in one place for every board — including the boards that
page inside a single search, which is exactly where a copied delay was easiest to forget.

Every board is now async. The three that fetch over plain HTTP call their existing `urllib`
code through `asyncio.to_thread` rather than gaining an async client: a board makes one request
and then waits fifteen to sixty seconds, so there is nothing concurrent to win, and
`aiohttp`/`httpx` are only present transitively today.

## What stays duplicated

`browser_config`, `crawl_config` and the card parsers. Every board's markup is quirky in its own
way and a fix for Totaljobs must not be able to break Reed. That was always the real content of
"duplicated by design" in CLAUDE.md; `slug` and `dedupe` were on that list by association and
were measured to have no per-board variation at all.

**This is not a framework.** A board is a module that knows one site. It is not a configuration
of a generic scanner, and adding a tenth board should not mean extending an abstraction — it
means writing a parser and a generator that yields what it fetched.

## Consequences

- The invariants have a test surface. Fifteen tests now cover them directly — a capture is
  written for every request and carries the stamp, the budget stops the board fetching again,
  requests are spaced, an empty page fails the run, the report filenames match the contract, a
  disabled board refuses. The source-grepping test is deleted.
- `scan_lock.hold` and `run_record.record` appear once in the project.
- Reed stopped building its own searches. It was the last board not using `build_board_urls`,
  which is how it missed the `paired()` fix and asked for two of its five titles (see ADR-0001).
  Its raw captures are now named like every other board's — hyphens rather than underscores.
- Roughly 500 lines left the board modules.
