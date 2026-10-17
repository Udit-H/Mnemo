"""Sync Gmail into the graph: threads, people, orgs and relationship stats (headers only, no LLM).

Run from the repo root:
    python -m scripts.gmail_sync                 # last 90 days, up to 500 threads
    python -m scripts.gmail_sync --days 180 --max 1000
    python -m scripts.gmail_sync --stats-only    # recompute people stats from the graph, no Gmail fetch
Re-running is safe (MERGE); stats are recomputed from everything in the graph.
"""

import argparse
import sys
import time

from dotenv import load_dotenv

from mnemo.gmail.client import fetch_threads, get_service, my_addresses
from mnemo.graph.db import get_driver
from mnemo.graph.email import recompute, summary, top_contacts, write_threads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max", type=int, default=500)
    parser.add_argument("--stats-only", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    start = time.monotonic()
    service = get_service()
    me = my_addresses(service)
    with get_driver() as driver:
        if args.stats_only:
            recompute(driver, me)
        else:
            print(f"Fetching up to {args.max} threads from the last {args.days} days for {', '.join(me)}…")
            threads = fetch_threads(service, f"newer_than:{args.days}d -in:chats", args.max,
                                    progress=lambda done, total: print(f"\r  {done}/{total} threads", end="",
                                                                       flush=True))
            print(f"\n  {len(threads)} threads, {sum(len(t.messages) for t in threads)} emails")
            write_threads(driver, threads, me)
        print("Graph:", ", ".join(f"{r['n']} {r['k']}" for r in summary(driver)))
        print("\nWho matters most (from how you actually reply):")
        for r in top_contacts(driver):
            print(f"  {r['importance']:.2f}  {r['who'][:28]:<28} {r['domain'] or '':<22} "
                  f"in {r['received']:>3} · out {r['sent']:>3} · reply {r['reply_rate']} · {r['avg_reply_hours']}h")
    print(f"\nDone in {time.monotonic() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
