# Domain language

The words this project uses for its own parts. They are already in the code; this is where they
are defined. Write these, not their synonyms — a name that drifts is a name that stops being
searchable.

## Source / Board

One place jobs are read from: Reed, LinkedIn, a mailbox, a feed. **Board** in code and on disk
(`boards:` in `config.yml`, `outputs/<board>/`, the `board` field on every row); **source** in
anything a person reads. They mean the same thing. The directory is still `reed_crawler/` for
the same historical reason.

A board is a module that knows one site. It is not a configuration of a generic scanner: adding
one means writing a parser, not extending an abstraction.

## Spec

One search — a title paired with a location, and the URL that asks for it:

```json
{"board": "reed", "title": "applied ai engineer", "location": "leeds",
 "url": "https://www.reed.co.uk/jobs/applied-ai-engineer-jobs-in-leeds?proximity=50"}
```

`board_config.build_board_urls` produces them and is the only thing that does. Boards differ in
where their specs come from — the product of two config groups, explicit `search_params` rows,
a feed's own category vocabulary, or nothing at all for the email board — and every one of those
comes back in this shape.

A spec is one search, not one request. Most boards spend one request on it; LinkedIn and the
feeds page within it.

## Lead

One posting as a board found it. Fifteen fields, defined once in `reed_crawler/lead.py`, and the
shape of every row in every report — so it is the contract `dashboard/aggregate.py` reads back
and `ingest_jobspy.py` filters.

Not a job *application*, and not a job the user cares about: a report is everything a board
showed. Which leads are worth opening is decided downstream, by career-ops' `portals.yml`.

## Scan

One board asking its host everything its config names, once. Holds that board's lock for its
whole life, writes one run record, and produces one report. Started by the timer, by the
dashboard, or by hand; all three go the same way.

`scan_run.py` is what a scan is apart from the asking. `scan_search.py` is the asking.

## Capture

The raw evidence of one request, under `outputs/<board>/raw/`, named for the search and stamped
with the run. Nothing reads them back. They are what you open when a board goes quiet and you
need to know whether the parser broke or the jobs did — which is why the stamp matters: without
it a scan destroys the evidence for the run you are trying to investigate.

## Report

What a scan produced, under `outputs/<board>/reports/`, as `<board>_<stage>_<YYYY-MM-DD>_<HHMMSS>.json`.
The filename is a contract; stage discovery and every aggregation parse it. Two stages: `raw` is
every lead found, `deduped` is one row per advert, best paid first.

The dashboard holds no database. It re-reads these per request, so it can never disagree with
what the crawler actually produced.

## Run record

How a scan ended — running, done, failed, busy, interrupted — in `outputs/state/runs.json`,
written by `run_record` for every scan whatever started it. Distinct from the report: a scan
that failed wrote no report, and that is the kind most worth seeing.

## Related

- `CLAUDE.md` — the invariants, each learned from a bug.
- `docs/adr/` — decisions that reversed earlier reasoning, and why.
