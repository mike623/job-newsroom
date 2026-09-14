"""Drive one board's searches, holding what must not vary between them.

A board knows how to reach its host and how to read a card. Everything around that is the same
for all of them and is the part that must not be got wrong: a raw capture carries the run stamp,
an unusable page is a failure rather than an empty result, and requests to one host are spaced
out. Those were written into each board separately, so each was one edit away from breaking an
invariant nothing would have noticed.

The board yields its fetches; this consumes them. That direction matters. A generator is lazy,
so the next request does not happen until this has classified the last one and waited — which
means the rate and the request budget are held here, for every board, including the two that
page within a single search. A board that pages still cannot page faster than this allows.

What the board keeps is what genuinely differs: whether a search costs one request or several,
whether an empty reply is worth retrying, what a block looks like and what to do about it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from board_config import jittered, raw_capture_stem
from lead import Lead, slug
import scan_run


@dataclass
class Fetched:
    """One request a board made, and what it got.

    `captures` is the evidence to keep, keyed by file extension — markdown and HTML for a
    crawled page, `json` for an API. Nothing reads these back; they are what you open when a
    board goes quiet and you need to know whether the parser broke or the jobs did.

    `suffix` distinguishes several requests made for one search — a page number, a paging
    offset — so their captures do not overwrite each other.
    """
    response: object
    leads: list[Lead] = field(default_factory=list)
    captures: dict[str, str] = field(default_factory=dict)
    suffix: str = ""


Fetcher = Callable[[dict], AsyncIterator[Fetched]]


async def search(run: scan_run.Run, specs: list[dict], fetches: Fetcher, *,
                 delay: float, pages_per_spec: int = 1) -> None:
    """Ask a board for every search in `specs`, one request at a time.

    `pages_per_spec` is the only bound on how many requests one search may cost. With the cap on
    the number of searches gone, it is what keeps a scan's size in hand — so it is enforced here
    by abandoning the board's generator rather than trusted to the board's own loop.
    """
    for spec in specs:
        name = "__".join(slug(part) for part in (spec["title"], spec.get("location")) if part)
        requests = 0
        async for fetched in fetches(spec):
            stem = raw_capture_stem(f"{name}__{fetched.suffix}" if fetched.suffix else name,
                                    run.stamp)
            for extension, text in fetched.captures.items():
                (run.raw_dir / f"{stem}.{extension}").write_text(text or "", encoding="utf-8")
            run.health.record(fetched.response)
            run.leads.extend(fetched.leads)

            requests += 1
            if requests >= pages_per_spec:
                break
            await asyncio.sleep(jittered(delay))
        await asyncio.sleep(jittered(delay))
    run.searches = len(specs)
