"""Starting scans, and bounding how many run at once.

Split out of the dashboard once the dashboard stopped being the only thing that starts a scan.
Execution lives here; `runner.app` exposes it over HTTP so a scheduler outside this process —
a cron container, another machine — triggers exactly what a button does.

Reading a scan is not in here. History, status and the live log are files under `outputs/`
(`run_record`, and the per-run log), so any process with the directory can follow a scan it did
not start. That is why the dashboard still imports `runner.scans` for reads and calls the API
only to start something.
"""
