"""Run the scan runner: `python -m runner`.

Binds to loopback by design, like the dashboard: every endpoint starts a crawl, so this must not
be reachable from the network. In a container loopback reaches nothing, so RUNNER_HOST opens the
bind there and compose keeps the service private by giving it no published port at all.
"""
from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m runner", description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    host = os.environ.get("RUNNER_HOST", "127.0.0.1")
    print(f"Runner on http://{host}:{args.port}")
    uvicorn.run("runner.app:app", host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
