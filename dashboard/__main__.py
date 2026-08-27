"""Run the dashboard: `python -m dashboard`.

Binds to loopback by design. The service can start scans, so it must not be reachable from
the network; --host is deliberately not exposed.

In a container loopback is the container's own, which nothing outside it can reach, so
DASHBOARD_HOST=0.0.0.0 opens the binding there. The property is then held one layer out, by
publishing the port on the host's loopback only — see docker-compose.yml.
"""
from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dashboard", description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true", help="restart on code and template changes")
    args = parser.parse_args()

    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    print(f"Dashboard on http://{host}:{args.port}")
    uvicorn.run(
        "dashboard.app:app",
        host=host,
        port=args.port,
        reload=args.reload,
        reload_dirs=["dashboard"] if args.reload else None,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
