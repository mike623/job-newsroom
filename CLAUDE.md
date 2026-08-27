# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The project is **Job Newsroom**, on GitHub at `mike623/job-newsroom`. Its first home,
`mike623/job-board-crawler`, is archived: read-only, still public, and where the pre-rebrand
issues and pull requests stayed behind. Commits before the rebrand reference PR numbers that
resolve there, not here.

Internally nothing was renamed. The directory is still `reed_crawler/`, the config key is still
`boards:`, and `outputs/<board>/` is still the layout on disk. Read "board" and "source" as the
same thing; write "board" in code and "source" in user-facing copy.

## Commands

Scripts run as file paths from the repo root using the venv interpreter (not `python -m`, no console scripts). The dashboard is the exception — it is a package.

```bash
# Setup
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt
crawl4ai-doctor
cp config.example.yml config.yml       # config.yml is gitignored

# Dashboard
.venv/bin/python -m dashboard                  # 127.0.0.1:8080
.venv/bin/python -m dashboard --reload         # dev
.venv/bin/python -m runner                     # 127.0.0.1:8081, only needed with RUNNER_URL set
docker compose up -d --build                   # dashboard + runner + timer, 127.0.0.1:8080

# Scans (the runner shells out to exactly these)
.venv/bin/python reed_crawler/run_reed_scan.py --config config.yml [--limit N]
.venv/bin/python reed_crawler/totaljobs_pipeline.py scan --config config.yml [--limit N]
.venv/bin/python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
.venv/bin/python reed_crawler/haystack_pipeline.py scan --config config.yml [--limit N]
.venv/bin/python reed_crawler/indeed_pipeline.py scan --config config.yml --allow-disabled
.venv/bin/python reed_crawler/adzuna_pipeline.py scan --config config.yml --allow-disabled
.venv/bin/python reed_crawler/email_pipeline.py scan --config config.yml --allow-disabled [--limit N] [--mark-read]

# Scheduling
.venv/bin/python reed_crawler/scan_all.py --due --config config.yml   # what the timer runs
.venv/bin/python reed_crawler/install_timer.py [--install|--uninstall]

# Fold a report into the downstream career-ops pipeline (a person's command — it writes outside this repo)
.venv/bin/python reed_crawler/ingest_jobspy.py --latest email --dry-run
.venv/bin/python reed_crawler/ingest_jobspy.py outputs/email/reports/email_deduped_<stamp>.json

# Validate generated URLs without crawling — fast check after config or URL-builder edits
.venv/bin/python reed_crawler/board_config.py

# Checks
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_salary.py::test_observed_formats
.venv/bin/python -m py_compile reed_crawler/*.py dashboard/*.py
```

## Architecture

Two halves. `reed_crawler/` collects listings; `dashboard/` renders them. They share nothing but the files on disk.

```
config.yml → scan (per-board lock, jittered delays)
               ├─ outputs/<board>/raw/<search>__<stamp>.{md,html}
               └─ outputs/<board>/reports/<board>_deduped_<stamp>.json
                        │
                        ▼  re-read per request, no index, no cache
                  dashboard  → / · /jobs · /jobs/<board>/<id> · /runs · /export.csv
                             → POST /scan/<board>, /scan-all  (subprocess + SSE)
```

**Crawler modules.** `board_config.py` builds every board's URLs and owns `run_stamp`, `raw_capture_stem` and `jittered`. `salary.py` and `scan_lock.py` and `scan_health.py` are shared. Each board then has its own parsing: `reed_utils.py` + `run_reed_scan.py`, `totaljobs_pipeline.py`, `talent_pipeline.py`, `indeed_pipeline.py`, `adzuna_pipeline.py`, `haystack_pipeline.py`, `email_pipeline.py`.

**Adzuna is an API, not a crawl.** `adzuna.co.uk` answers every automated fetch with a CloudFront 403 — curl and headless Chromium alike, any user agent — so `adzuna_pipeline.py` reads Adzuna's free JSON search API instead. No crawl4ai, no browser, no card parsing, and pay arrives as numbers so `salary.py` is not asked to parse it back out of prose. Credentials (free from developer.adzuna.com) live in `boards.adzuna.app_id` / `app_key` or `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`, and are attached at request time so they never reach a raw capture, a report or the log. Raw captures are `.json` here; everything downstream sees the same report shape as any other board.

**Email is a mailbox, not a crawl.** The boards already email their alerts, so `email_pipeline.py` reads the Gmail labels they are filed into through the `himalaya` CLI rather than fetching anything. It was ported from `career-ops/ingest-email-labels.mjs`, which wrote a JobSpy CSV; here the same extraction writes the ordinary deduped report, so the dashboard and cron treat it like any other board. It builds no URLs, so `board_config.build_board_urls` returns `[]` for it. Three axes stay separate: the **label** says which mailbox to read, the **sender** says which provider's URL rules apply (a mail filed by hand lands under the wrong label), and a **body signature** picks the template that knows that layout. Raw captures are `.json` holding each message's envelope and body.

**Haystack is HTML, not markdown.** Every other crawled board is parsed from crawl4ai's markdown. haystack.cv is a client-rendered SPA whose markdown runs a card's fields together with no separator, so `haystack_pipeline.py` parses the HTML with BeautifulSoup, anchored on the icon that labels each field (see Invariants). It is a `scan`-only module, like `talent_pipeline.py`.

**Dashboard modules.** `aggregate.py` is the only place that reads report JSON — jobs, board summaries, runs, filtering and sorting. `pipeline.py` reads the downstream workspace. `app.py` is routes only; `templates/` extends `base.html`. `runner_client.py` carries a start request to the runner.

**Runner modules.** `runner/scans.py` runs scans as subprocesses and persists their records; `runner/pool.py` bounds concurrency; `runner/app.py` exposes both over HTTP. Starting and following a scan are split deliberately: **only one process may spawn scans**, because `scan_lock` proves a lock live by signalling the pid that wrote it and that answer is meaningless across PID namespaces — two spawners scan the same board twice. Following is a file read (`run_record`, the per-run log), so the dashboard imports `runner.scans` directly for history, status and the live log stream, and calls the API only to start something. With `RUNNER_URL` unset there is no separate runner and the dashboard spawns in-process, which is how the project runs natively.

**Duplicated by design.** `slug`, `dedupe`, `browser_config` and `crawl_config` are copy-pasted across the board modules with per-board variations. Each board's markup is quirky in its own way, and keeping them independent means a fix for Totaljobs cannot break Reed. When changing crawl behaviour, decide explicitly whether it applies to one board or all, and edit each copy.

## Invariants

Each of these was learned from a bug. Breaking one silently corrupts data or gets a board blocked.

- **Per-host request rate is the safety property, not worker count.** Boards are separate hosts, so scanning them concurrently is free. Within a host the limit is `max(1, len(proxies))` — splitting by search term changes *what* is asked for, not *how often*, because rate limits are per IP. Never let pool size govern this.
- **Raw captures carry the run stamp.** They were once written to a deterministic name, so each scan destroyed the previous evidence for that search and concurrent scans corrupted each other. `raw_capture_stem` exists for this.
- **An empty page body is a failure, not zero results.** A crawl can return success with nothing in it. `scan_health` classifies this so a board cannot silently stop producing data.
- **One scan per board.** The lock lives in the scan entrypoints so the external cron inherits it without being modified. Exit 75 means busy, not broken.
- **The downstream workspace is written to only when a person asks.** `dashboard/pipeline.py` only ever reads it, and a test asserts nothing under it is modified. `reed_crawler/ingest_jobspy.py` is the sole writer, reachable from the terminal or the dashboard's `/ingest` button — never from `scan_all.COMMANDS`, so no schedule can reach it. It appends inside the pipeline's Pending section; appending at the end of the file buries entries under the processed section, where career-ops never looks.
- **An alert mail whose template is unrecognised is named, not guessed at.** A digest subject names one of its 25 jobs, so attributing it to all of them invents data — those rows get `Job lead (email)` instead, and the run prints the label, id and subject so a changed layout reads as work to do rather than as missing jobs.
- **Per-recipient links are stripped before a lead is kept.** Totaljobs wraps a posting in `/v2/magiclink/exchange?magicLink=<JWT>`, LinkedIn and Jobright append per-email tracking, and Totaljobs digests link through `totaljobsmail.com` trackers that must be resolved by reading `Location` headers only — fetching the destination hits Cloudflare. Untouched, one posting yields a different URL in every mail and dedup never fires.
- **Where a link is not an identity, the card is.** Indeed's sponsored links (`/pagead/clk`) carry no job id and a per-impression `ad` blob, so the same advert arrives under a different URL every morning; hashing the URL reported it as a new job daily. `job_id_for` falls back to title and company for those. Indeed's match mail also wraps every link in `cts.indeed.com/v3/<blob>`, which is a base64url gzip of `{"u": "<destination>"}` — it is decoded locally, never fetched, because Indeed blocks automated traffic hard.
- **An email lead's id comes from the posting URL, and only `email_pipeline` derives it.** The board forwards LinkedIn, Indeed, Totaljobs and Jobright postings rather than owning a URL shape, so a line in the downstream pipeline is matched back to a job by asking `email_pipeline.job_id_from_url` — `dashboard/pipeline.py` does not restate the rule as another regex, or the two drift and the actioned column quietly lies.
- **Report filenames are a contract.** `<board>_<stage>_<YYYY-MM-DD>_<HHMMSS>.json`. Stage discovery and every aggregation parse this shape.
- **Haystack is parsed from HTML, not markdown.** haystack.cv renders a whole card as one link whose text concatenates title, company, location, salary and posted date with no separator, so markdown cannot recover the fields. `parse_search_cards` walks the HTML and anchors each field on the lucide icon that labels it (`lucide-building2` → company, `lucide-map-pin` → location, `lucide-banknote` → salary, `lucide-clock` → posted). The surrounding utility classes are generated and will churn; the icon names are the stable part.
- **An empty Haystack scan can be the board, not the crawl.** Its search backend intermittently answers "Something went wrong loading jobs" on an otherwise healthy page; the scan retries once and then records zero leads. Its free-text `q` also matches loosely and appears to require every term, so multi-word titles return far fewer results than short ones. Pagination is a "Load More" button, so a scan sees only the first ~20 cards per search.

## Config

`config.yml` is the single input; `config.example.yml` is the committed template and `config.yml` is gitignored. Board sections reference named groups from `search.titles` / `search.locations`. The flat top-level keys at the bottom are a legacy fallback still read by `run_reed_scan.build_specs`.

`tests/test_talent_pipeline.py` asserts against **`config.example.yml`**, so editing its talent block or `max_pages_per_run` breaks that test — update both together, and keep the two files structurally in sync.

Keys that no longer do anything: every board's `full_jd` block, and Indeed's `reject_phrases`, which only ever ran against job-description text.

## Scheduling

Three files, each answering one question, and no scheduler that knows about the others:

| | |
| --- | --- |
| `config.yml` | *may* this board run at all |
| `outputs/state/schedule.json` | *when* should it run |
| `outputs/state/runs.json` | *when did* it last run |

The `/schedule` page also triggers by hand: **Run now** per board (the same `POST /scan/<board>` the overview uses) and **Run what is due now**, which asks `scan_all.due_boards` — the very question the timer asks — so pressing it proves what the timer will run rather than something that merely resembles it.

Both of the first two are edited from the dashboard's `/schedule` page: the **Enabled** box writes `boards.<name>.enabled` through `board_config.set_board_enabled`, which edits that one line of `config.yml` and re-parses the result before keeping it — the file is hand-commented and is the project's only input, so it is never rewritten from parsed YAML. The **Scheduled** box and the times write `schedule.json`.

A launchd agent (`com.mikewong.job-crawler`, installed once by `install_timer.py`) runs
`scan_all.py --due` every ten minutes. That command intersects the config's enabled boards with
`schedule.due()` and scans what is left, through the same `COMMANDS` table, locks and run records
as any other scan. Changing the timetable is a JSON edit from the dashboard's `/schedule` page —
the agent itself is written once and never touched again.

**launchd, not crontab.** The email board reads its Gmail password from the login keychain
(`security find-generic-password`), which needs the user's session, and launchd runs a missed
interval after the machine wakes where cron simply skips it.

**In Docker there is no launchd.** `docker-compose.yml` runs `runner`, `dashboard` and a
`timer` that is a `curl` loop over `POST /scan-due` — the same question `scan_all --due` asks.
Any scheduler can replace it, because triggering a scan is an HTTP request there rather than a
process to spawn. `JOB_CRAWLER_TIMER` tells `schedule_view.timer_state` what the timer is, since
`install_timer.is_loaded` can only ever answer for launchd. The email board does run there: the
runner image carries a Linux `himalaya`, and `secrets/` (mounted read-only at `/run/himalaya`,
named by `HIMALAYA_CONFIG`) replaces the macOS keychain with a password file — a deliberate
downgrade, since a Gmail app password is full account access and a keychain read needs an
unlocked session. Do not run the container and the Mac against one `outputs/`: `scan_lock`
compares pids, and a container pid means nothing to a native process, so both would scan the
same board.

`schedule.py` holds the rules and nothing else repeats them:

- A slot belongs to its own day. A machine asleep at 07:00 that wakes at 09:00 still catches up;
  one that stays off until tomorrow has missed that day rather than scanning the moment it starts.
- Three missed mornings produce **one** scan, not three.
- A scan started by hand or by the dashboard satisfies the slot — the schedule asks that the
  board be scanned, not that the timer be the one to do it.
- A *busy* run does not count, because it never asked the board anything. A *failed* one does:
  retrying every ten minutes is the worst possible response to a board that has begun blocking us.

## Downstream ingest

`ingest_jobspy.py` is the only path from this project into career-ops. It takes a deduped report — or a JobSpy CSV, which is what it was ported from — filters it against **career-ops/portals.yml**'s `title_filter` and `location_filter`, drops rows already in `data/pipeline.md` or `data/scan-history.tsv` and rows that are one req posted to several cities, then appends the survivors to both files.

Relevance is defined downstream on purpose: portals.yml is what career-ops' own scanner filters on, so this project never holds a second opinion about which jobs matter. A report is everything a board showed; the pipeline is what is worth opening.

It is not part of a scan. `--dry-run` prints the lines it would add and writes nothing.

The dashboard's `/ingest` page previews each board by calling that same dry run: a summary table of boards with their counts and buttons, and a filterable table of the individual jobs, each row carrying the exact pipeline line it will become as its link title. Filtering narrows what is read, never what is sent — ingest appends a whole report, so the buttons always state the full count. Its per-board and **Send all** buttons shell out to the script rather than reimplementing it. Sending several boards runs them one after another: they all append to the same `pipeline.md`, which has no lock because it was written to be edited by one person at a time. A board whose newest report changed since the preview was drawn is skipped rather than appended unseen.

## Sunset code

Full job descriptions are no longer fetched or exported; the project collects listings. These modules are kept for reference, marked `SUNSET` in their first docstring, and wired to nothing: `enrich_full_jds.py`, `export_to_career_ops.py` (superseded by `ingest_jobspy.py`, which needs no full JD), `test_full_jd.py` (a probe, not a test), `manual_totaljobs_crawl4ai_import.py`, and the enrich/export subcommands inside `totaljobs_pipeline.py` and `indeed_pipeline.py`. Do not build on them without checking whether that is intended.

## Cron

A launchd agent calls one command and nothing else:

```bash
.venv/bin/python reed_crawler/scan_all.py --due --config config.yml
```

Without `--due` the same command scans every enabled board at once, which is what a person wants
and what an external cron would run.

`scan_all.py` reads `boards.<name>.enabled` and runs each enabled board's entrypoint as a subprocess, so enabling or disabling a board is a config edit and never a change to a script outside this repo. It exits 0 when every board succeeded or was already locked (75), and 1 if any board failed.

`COMMANDS` in `scan_all.py` is the single table of how each board is scanned; `dashboard/scans.py` imports it so the button and the cron cannot drift. Do not add `--allow-disabled` to it — that flag is for manual smoke tests and would defeat the config.

## Agent skills

### Issue tracker

GitHub Issues on `mike623/job-newsroom`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
