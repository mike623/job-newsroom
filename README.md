# :mag: job-board-crawler

![Python version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
[![Crawl4AI](https://img.shields.io/badge/built%20with-Crawl4AI-6f42c1.svg)](https://github.com/unclecode/crawl4ai)
[![FastAPI](https://img.shields.io/badge/dashboard-FastAPI-009485.svg)](https://fastapi.tiangolo.com/)
![Boards](https://img.shields.io/badge/boards-Reed%20%7C%20Totaljobs%20%7C%20Indeed%20%7C%20Talent.com%20%7C%20Adzuna-success.svg)

> Watches five UK job boards, keeps track of what appears and disappears, and shows you the difference

Job boards render their listings with JavaScript, rate-limit aggressively, and never tell you what changed since yesterday. This drives a real headless browser through Reed, Totaljobs, Indeed and Talent.com — slowly enough not to get blocked — reads Adzuna from its JSON API, then serves a local dashboard over everything it has collected.

Because every scan is kept, the dashboard can answer things a single search cannot: which jobs are new, which have quietly disappeared, how long one has been open, and which you have already dealt with.

```bash
python -m dashboard          # http://127.0.0.1:8080
```

## Features

- **Five boards, one config.** Reed, Totaljobs, Indeed, Talent.com and Adzuna, all driven from a single `config.yml`.
- **Job-centric history.** Every job appears once, with when it was first and last seen and how many scans have seen it.
- **Structured salary.** Free text like `£70k - 85k per year` or `71,250-118,000 Annual` becomes a sortable minimum, maximum and period.
- **Scan from the browser.** Start a board and watch its output stream live; the scan survives closing the page.
- **Slow by design.** Per-host request rates are bounded and delays are jittered, so concurrency never costs a board extra traffic.
- **One scan per board.** A file lock the cron inherits, so a manual scan and a scheduled one cannot collide.
- **Knows what you've actioned.** Cross-references a downstream workspace, read-only, so you can filter to what is untouched.
- **Honest failures.** A page that comes back empty is reported as a broken scan, not as a search with no matches.

## How it works

```
                    ┌──────────────┐
   config.yml ─────▶│     scan     │  one lock per board, jittered delays
                    └──────┬───────┘
                           │ writes, never overwrites
                           ▼
        outputs/<board>/raw/<search>__<stamp>.{md,html}   ← evidence
        outputs/<board>/reports/<board>_deduped_<stamp>.json
                           │
                           │ re-read on every request, no index
                           ▼
                    ┌──────────────┐
                    │  dashboard   │──▶ overview · jobs · runs · CSV
                    └──────────────┘
```

Nothing is cached and nothing is precomputed. The dashboard reads the same report files the crawler writes, so it cannot disagree with them, and deleting the service loses nothing.

## Getting started

### Prerequisites

- Python 3.11 or later
- A Chromium install for Playwright, which `crawl4ai-doctor` sets up for you

### Installation

```bash
git clone git@github.com:mike623/job-board-crawler.git
cd job-board-crawler

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

crawl4ai-doctor              # installs and verifies the headless browser
cp config.example.yml config.yml
```

`config.yml` is gitignored — it holds your salary targets and locations.

### Configuration

Titles and locations are declared once in named groups, then referenced per board:

```yaml
search:
  titles:
    primary: [senior software engineer, backend developer]
  locations:
    core: [london, manchester]

boards:
  reed:
    enabled: true
    proxies: []             # egress identities; empty means one crawl at a time
    title_groups: [primary]
    location_groups: [core]
    proximity: 50
    max_pages_per_run: 8
```

Check what URLs your config produces without crawling anything:

```bash
python reed_crawler/board_config.py
```

> [!IMPORTANT]
> The low `max_pages_per_run` and multi-second `delay_seconds` defaults are deliberate. Job boards block scrapers that move quickly. Raise them gradually and expect to be blocked if you don't.

## The dashboard

```bash
python -m dashboard              # http://127.0.0.1:8080
python -m dashboard --reload     # restart on code and template changes
```

Bound to loopback only, with no host option: it can start scans, so it must not be reachable from the network.

| Page | What it answers |
| --- | --- |
| `/` | How is each board doing, and what shall I scan? |
| `/jobs` | What is out there, and what have I not looked at? |
| `/jobs/<board>/<id>` | What is this advert, and how long has it been open? |
| `/runs` | Did the crawler break? |
| `/export.csv` | Give me the current filter as a spreadsheet |

Filter jobs by board, by a pay floor, and by whether they have already reached your downstream workspace. Sort by pay, dates, company or how many times a job has been seen.

### Running scans

Scans take minutes, so they run as background subprocesses — exactly the commands the cron runs. Output streams to the browser, and closing the page does not stop the scan.

Scanning all boards runs them concurrently. They are separate hosts, so this costs no board a single extra request: within one host the limit stays at one crawl unless proxies are configured, because rate limits are per IP and splitting by search term changes what you ask for, not how often.

> [!TIP]
> With one worker per board the pool saturates at the number of enabled boards. It is there for queueing and visibility, not for speed.

## The command line

The dashboard is a convenience; every scan is a plain script.

```bash
python reed_crawler/scan_all.py --config config.yml [--limit N]   # every enabled board
python reed_crawler/scan_all.py --due --config config.yml          # only what the schedule says is due

python reed_crawler/run_reed_scan.py --config config.yml [--limit N]
python reed_crawler/totaljobs_pipeline.py scan --config config.yml [--limit N]
python reed_crawler/talent_pipeline.py scan --config config.yml --limit 1
python reed_crawler/indeed_pipeline.py scan --config config.yml [--allow-disabled]
python reed_crawler/adzuna_pipeline.py scan --config config.yml [--allow-disabled]
python reed_crawler/email_pipeline.py scan --config config.yml [--allow-disabled] [--mark-read]

python reed_crawler/ingest_jobspy.py --latest email --dry-run   # what would reach career-ops
python reed_crawler/ingest_jobspy.py outputs/email/reports/email_deduped_<stamp>.json
```

`scan_all.py` takes its board list from `boards.<name>.enabled`, so turning a board on or off is a config edit rather than a change to the cron. A single board's script still runs on its own; `--allow-disabled` scans Indeed when the config has it off, for manual smoke tests only.

`--limit N` caps the search pages for one run. Every scan takes its board's lock, so the cron, a terminal and the dashboard all stay out of each other's way; a scan that finds the board busy exits **75**, and one where no search returned a usable page exits non-zero.

### Board reference

| Board | Parsed from | Notes |
| --- | --- | --- |
| **Reed** | Markdown headings | Full field coverage; the most reliable board |
| **Totaljobs** | Markdown cards | Anchored on the card's `more` line |
| **Indeed** | HTML | Card links are click wrappers with no job id; the HTML has one |
| **Talent.com** | Markdown cards | Needs a seed result `id` to hydrate — see below |
| **Adzuna** | JSON API | Not crawled at all: the site 403s every bot. Needs free API credentials — see below |
| **Email** | Gmail labels | Not crawled at all: reads the alert mail the boards already send — see below |

### Talent.com

Talent.com can return an unhydrated shell for `https://uk.talent.com/jobs?k=...&l=...`. Supplying a live result `id` forces the list to render:

```yaml
boards:
  talent:
    max_pages_per_run: 2
    delay_seconds: 60
    search_params:
      - k: Senior Software Engineer
        l: london
        id: "611275213865225891"   # seeds hydration
```

It rate-limits harder than the others; keep the volume low.

### Adzuna

`adzuna.co.uk` sits behind CloudFront, which answers every automated request with a bare 403 — curl and headless Chromium alike, whatever user agent is offered. There is no markup to parse, so this board reads Adzuna's [JSON search API](https://developer.adzuna.com/) instead. Register free, then:

```yaml
boards:
  adzuna:
    enabled: true
    app_id: "..."          # or export ADZUNA_APP_ID
    app_key: "..."         # or export ADZUNA_APP_KEY
    title_groups: [primary]
    location_groups: [core]
    distance: 30           # kilometres
    results_per_page: 50   # the API's maximum
```

`config.yml` is gitignored, so credentials are safe there; the environment variables win when set. They are attached at request time only, so no capture, report or log line ever contains the key. Pay comes back as numbers rather than as advertiser prose — where `salary_is_predicted` is set, the salary is Adzuna's own estimate and says so.

### Email alerts

The boards email their own alerts, so this board reads them instead of asking for the same jobs twice. It needs the [`himalaya`](https://github.com/pimalaya/himalaya) CLI configured against the account (`brew install himalaya`, then run `himalaya` once):

```yaml
boards:
  email:
    enabled: true
    messages_per_label: 25   # newest N per label per run
    max_age_days: 14
    mark_read: false         # flag mail that produced leads as seen, so a rerun skips it
    labels:
      - label: job/discovery/indeed
        provider: indeed
      - label: job/discovery/linkedin
        provider: linkedin
```

The label says which mailbox to read; the **sender** decides which provider's URL rules apply, because mail filed by hand lands under the wrong label; and a body signature picks the template that knows that sender's current layout. A digest lists 6-25 jobs under a subject naming one of them, so each job's title, company and location are read from the block its own link sits in — mail whose layout is not recognised is listed by id and subject at the end of the run rather than inheriting the subject.

Per-recipient links (Totaljobs magic links, LinkedIn and Jobright tracking) are reduced to the public posting, so the same job seen in five mails is one row. Indeed's `cts.indeed.com` click wrappers are decoded locally — the destination is inside the link — and its sponsored postings, which carry no job id at all, are identified by title and company instead so they do not reappear as new jobs each morning.

### Sending leads downstream

A report is everything a board showed; `ingest_jobspy.py` decides what is worth opening and appends it to the career-ops pipeline:

```bash
python reed_crawler/ingest_jobspy.py --latest email --dry-run   # print, write nothing
python reed_crawler/ingest_jobspy.py --latest email
python reed_crawler/ingest_jobspy.py jobspy-export.csv --max-age-days 30
```

Relevance comes from **career-ops/portals.yml** — the same `title_filter` and `location_filter` its own scanner uses — so there is one definition of a relevant job rather than two. Rows already in `data/pipeline.md` or `data/scan-history.tsv` are skipped, as is one req posted to several cities. It reads a JobSpy CSV export too, which is the shape it was ported from.

The dashboard's **Ingest** page shows the same preview as two tables: boards with their counts
and a button each (plus **Send all**), and every candidate job as a filterable row — by board, or
by text across title, company and location. Filtering changes what you read, not what a button
sends, since ingest appends a whole report. Nothing here ever happens on a schedule — the
scheduler has no route into the workspace at all, and a test asserts it.

## Scheduling

Install the timer once:

```bash
python reed_crawler/install_timer.py --install     # a launchd agent, every 10 minutes
```

It runs `scan_all.py --due` and nothing else. What that scans is decided by three files, each
answering one question — `config.yml` whether a board may run, `outputs/state/schedule.json` when
it should, and the run history when it last did — so **changing the timetable never means touching
launchd again**. Edit it on the dashboard's **Schedule** page. Each row has two boxes, and they mean different
things: **Enabled** is `boards.<name>.enabled` in `config.yml` — whether the board runs at all,
for the cron, the terminal and "scan all" alike — and **Scheduled** is this timetable, when the
timer should start it. A board that is scheduled but not enabled never runs. Times are either
fixed (`07:00, 18:30`) or an interval with an optional active window, plus the days they apply.

The same page triggers scans by hand: **Run now** on any enabled board, and **Run what is due
now**, which runs exactly what the timer would run at its next tick.

A few behaviours worth knowing:

- Three missed mornings produce one scan, not three.
- A scan you started yourself counts — the schedule asks that a board be scanned, not that the
  timer do it.
- A failed scan waits for its next slot instead of retrying every ten minutes, which is the last
  thing a board that has started blocking you needs.
- The Schedule page and the overview say plainly when the agent is not loaded, because a
  timetable nothing reads looks exactly like one that works.

launchd rather than cron: the email board reads its Gmail password from the login keychain, which
needs your session, and launchd runs a missed interval after the machine wakes.

## Docker

`docker compose up -d` runs three containers built from one image:

```bash
cp config.example.yml config.yml       # compose mounts it; the image never bakes it in
docker compose up -d --build
open http://127.0.0.1:8080
```

```
timer     ──POST /scan-due──▶  runner  ──spawns──▶  crawls
dashboard ──POST /scan/…────▶  runner
dashboard ──reads outputs/──▶  history, status, live logs
```

**The runner** is the only thing that starts a scan, and it has no published port — every one of
its endpoints begins a crawl, so it is reachable from the compose network and nowhere else.
**The dashboard** asks it (`RUNNER_URL`) instead of spawning, and keeps reading `outputs/`
directly for everything else. **The timer** is four lines of `curl` in a loop; it needs no Docker
socket and no privileges, because triggering a scan is now an HTTP request rather than a process
to spawn. Point ofelia, a host cron, or a Kubernetes CronJob at `POST /scan-due` instead and
nothing else changes.

Both services mount three things, and the distinction matters:

```yaml
    volumes:
      - ./outputs:/app/outputs      # data — everything a scan produces
      - ./config.yml:/app/config.yml
      - .:/app                      # convenience — the checkout over the image's copy
```

`outputs/` is **never copied into the image** (`.dockerignore` skips it). Every report, raw
capture, run record, lock and log is written straight through to the checkout, so the containers
hold no state of their own and can be destroyed and rebuilt without losing a scan.

The third line is separate on purpose. It puts the working tree over the image's copy of the
source, so editing a board pipeline takes a `docker compose restart <service>` rather than a
rebuild — but it also means the running container is no longer what the image says it is. Delete
that one line to run the image exactly as built; the two data mounts above are what must stay.

One `Dockerfile`, two targets. Both share every Python dependency; they differ only in Chromium,
which the runner needs and the dashboard never launches — 2.38GB for the runner against 996MB for
the dashboard, and the shared layers are stored once. Chromium is installed before any source is
copied, so editing code rebuilds only the last layer of each target.

### Why the runner is separate, and why only one of it

`scan_lock` decides whether a board is already being scanned by signalling the pid that wrote the
lock. That answer is only true inside the namespace owning the pid, so two containers that both
spawn scans would each read the other's live lock as stale and scan the same board twice —
doubling the request rate that every delay in the crawler exists to avoid. One spawner keeps the
lock honest; everyone else asks it.

Following a scan needs no such care. The run record and the log are files under `outputs/`, so
the dashboard streams a scan it did not start straight off the shared volume.

### The rest

- **The published port binds `127.0.0.1` on the host.** The dashboard can start scans, so it must
  not be reachable from the network. Inside the container it listens on `0.0.0.0`
  (`DASHBOARD_HOST`) because a container's own loopback is reachable by nothing; the port
  publication is what keeps it private. Changing it to `8080:8080` puts a scan trigger on your LAN.
- **`JOB_CRAWLER_TIMER` names the timer.** Without it the Schedule page looks for launchd, does
  not find it, and warns that nothing is reading the timetable.
- **`install_timer.py` is for a Mac** running the project natively. It has nothing to do with this.
- **Natively there is no runner.** With `RUNNER_URL` unset the dashboard starts scans in-process,
  exactly as before — one process, one terminal. Both paths end in the same code; only the
  transport differs.

### The email board in a container

It works, but it needs the one thing a container cannot inherit from macOS: your keychain.
Natively `himalaya` reads the Gmail app password with `security find-generic-password`, which
needs an unlocked GUI session. The runner image carries a Linux `himalaya` and reads the password
from a file instead:

```bash
mkdir -p secrets
cp secrets/config.example.toml secrets/config.toml    # fill in your address
security find-generic-password -a YOU@gmail.com -s himalaya-gmail-app-password -w > secrets/password
chmod 600 secrets/config.toml secrets/password
```

`./secrets` is mounted read-only at `/run/himalaya`, `HIMALAYA_CONFIG` points at it, and both
files are gitignored and excluded from the build context — nothing is baked into the image, and
nothing is passed as an environment variable, which `docker inspect` would print.

**That app password is full IMAP and SMTP access to the whole account**, not scoped to the job
labels. In the keychain it needs your session to read; in a file it does not. Treat the machine
holding `secrets/` accordingly, and revoke the password at myaccount.google.com if it is ever
shared or exposed.

Without those files the board fails at scan time with himalaya's own error and the other six are
unaffected — the container still starts.

### The downstream workspace

`career-ops` lives outside this repo, so the dashboard mounts it explicitly:

```yaml
      - ${CAREER_OPS_DIR:-../career-ops}:/workspace/career-ops   # dashboard only
```

with `CAREER_OPS_WORKSPACE=/workspace/career-ops` naming it inside. Set `CAREER_OPS_DIR` if your
checkout is not a sibling of this one. Without the mount, `/ingest` reports no workspace
configured — the container was looking for a sibling of `/app`.

It is mounted on the **dashboard and not the runner**, which makes the rule that no schedule may
write downstream a physical fact rather than a convention: the container that scans cannot reach
`pipeline.md` at all, however a scan is triggered.

Podman works too — `podman-compose up -d` — with no changes to the compose file.

## Outputs

Everything runtime lands under `outputs/`, which is gitignored:

```
outputs/<board>/raw/        page captures, one set per run
outputs/<board>/reports/    timestamped JSON per stage
outputs/state/locks/        which board is being scanned
outputs/state/runs.json     scans started from the dashboard
outputs/state/logs/         their output
```

Captures carry the run stamp, so scans accumulate rather than overwrite, and a capture can be matched to the report written beside it. When a parser starts returning nothing — usually a board changing its markup — those captures are what you diff.

## Testing

```bash
python -m pytest                          # ~150 tests, no network required
python -m py_compile reed_crawler/*.py dashboard/*.py
```

> [!NOTE]
> Tests assert against `config.example.yml`, not your personal `config.yml`. Adding a config key means adding it to both.

## Troubleshooting

**A scan reports `empty-body`.** The fetch succeeded but the page had nothing in it — usually transient, occasionally a block. The capture is still written; check it for a consent wall or a CAPTCHA. If every search in a run does this, the run exits non-zero.

**A scan exits 75.** The board is already being scanned, by the cron, a terminal or the dashboard. Nothing is wrong; wait for the other one.

**A board returns zero jobs with a healthy page.** The parser needs updating for changed markup. Diff the newest capture in `outputs/<board>/raw/` against an older one.

**Crawls hang.** Run `crawl4ai-doctor`, then set `crawl.headless: false` to watch the browser and see where it stalls.

The `probe_*.py` scripts are standalone single-URL crawls for testing a board in isolation. Modules marked `SUNSET` at the top are kept for reference and are not wired to anything.
