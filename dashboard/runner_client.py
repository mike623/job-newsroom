"""Ask the runner to start a scan.

Starting and following a scan are split on purpose. Following is a file read — the run record and
the log live under `outputs/`, so this process sees a scan whatever started it, and `runner.scans`
is imported directly for that. Starting is not: the process that spawns a scan owns its pid, and
`scan_lock` decides whether a lock is live by signalling that pid. One spawner keeps that answer
true, so this module carries the request there instead of doing it here.

With RUNNER_URL unset there is no separate runner and the dashboard starts scans itself, which is
how the project runs natively on a Mac. Compose sets it, because there the runner is its own
container. Both routes end in the same `runner.scans.Scans.start`; only the transport differs.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

TIMEOUT = 30          # starting a scan returns as soon as the child is spawned


class RunnerError(RuntimeError):
    """The runner refused, or could not be reached.

    Carries the runner's own status so a refusal it made deliberately — 409 for a board already
    being scanned — reaches the browser as that same answer rather than as a generic failure.
    """

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def base_url() -> str:
    return os.environ.get("RUNNER_URL", "").rstrip("/")


def enabled() -> bool:
    return bool(base_url())


def _post(path: str) -> dict:
    request = urllib.request.Request(f"{base_url()}{path}", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as refusal:
        detail = refusal.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise RunnerError(detail, status=refusal.code) from refusal
    except (urllib.error.URLError, OSError) as unreachable:
        raise RunnerError(f"the scan runner is not answering: {unreachable}") from unreachable
    return json.loads(body or "{}")


async def post(path: str) -> dict:
    """The blocking request, off the event loop — the dashboard serves other pages meanwhile."""
    return await asyncio.to_thread(_post, path)
