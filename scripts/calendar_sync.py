"""Sync Google Calendar into the graph: events, attendees, links to email threads/projects, meeting stats (no LLM).

Run from the repo root:
    python -m scripts.calendar_sync                  # 30 days back, 14 ahead
    python -m scripts.calendar_sync --back 90 --ahead 30
Needs Calendar access: re-run `python -m scripts.gmail_auth` once if it says a scope is missing.
Re-running is safe (MERGE); events cancelled in the window are removed.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from mnemo.calendar.client import fetch_events, get_calendar
from mnemo.gmail.client import get_service, my_addresses
from mnemo.graph.calendar import summary, upcoming, write_events
from mnemo.graph.db import get_driver
from mnemo.graph.email import recompute, top_contacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--back", type=int, default=30)
    parser.add_argument("--ahead", type=int, default=14)
    args = parser.parse_args()

    load_dotenv(".env")
    me = my_addresses(get_service())
    cal = get_calendar()
    now = datetime.now(timezone.utc)
    window = (now - timedelta(days=args.back), now + timedelta(days=args.ahead))
    events = fetch_events(cal, args.back, args.ahead)
    print(f"{len(events)} events ({args.back} days back, {args.ahead} ahead)")
    with get_driver() as driver:
        write_events(driver, events, me, window)
        recompute(driver, me)  # meetings feed CORRESPONDS_WITH.met and importance
        s = summary(driver)
        print(f"Graph: {s['events']} events ({s['upcoming']} upcoming), {s['attendances']} attendances, "
              f"{s['thread_links']} event→thread links, {s['project_links']} event→project links")
        print("\nNext 7 days:")
        for e in upcoming(driver):
            extra = " · ".join(filter(None, [", ".join(e["attendees"][:3]), ", ".join(e["projects"])]))
            print(f"  {e['start'].to_native():%a %d %b %H:%M}  {e['title'][:40]:<40} {extra}")
        print("\nWho matters most (email + meetings):")
        for r in top_contacts(driver, 8):
            print(f"  {r['importance']:.2f}  {r['who'][:28]:<28} in {r['received']:>3} · out {r['sent']:>3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
