"""Read important threads in full and extract summary, category, needs-reply, commitments, topics and links to
projects/goals from chat memory.

Run from the repo root:
    python -m scripts.email_extract                  # top 40 non-bulk threads of the last 60 days
    python -m scripts.email_extract --limit 80 --days 90 --force
Then: python -m scripts.embed   (embeds any new topics)
"""

import argparse
import sys
from collections import Counter

from dotenv import load_dotenv

from mnemo.extract.email_extractor import extract_thread, thread_transcript
from mnemo.gmail.client import fetch_thread_text, get_service, my_addresses
from mnemo.graph.db import database, get_driver
from mnemo.graph.email import context_names, threads_to_extract, write_thread_extraction
from mnemo.graph.queries import existing_goals, top_topics
from mnemo.llm import GroqPool

SUMMARY = """
MATCH (th:Thread) WHERE th.extracted_at IS NOT NULL
RETURN th.category AS category, count(*) AS threads, sum(CASE WHEN th.needs_reply THEN 1 ELSE 0 END) AS need_reply,
       sum(COUNT { (th)-[:HAS_COMMITMENT]->() }) AS commitments,
       sum(COUNT { (th)-[:IN_CONTEXT|RELATES_TO]->() }) AS linked_to_memory
ORDER BY threads DESC
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    service, llm = get_service(), GroqPool.from_env()
    me = my_addresses(service)
    stats = Counter()
    with get_driver() as driver:
        todo = threads_to_extract(driver, args.days, args.limit)
        contexts, goals, topics = context_names(driver), existing_goals(driver), top_topics(driver)
        print(f"Extracting {len(todo)} threads with {llm.extract_model}")
        for i, th in enumerate(todo, 1):
            try:
                messages = fetch_thread_text(service, th["id"])
                x, cached = extract_thread(llm, th["id"], thread_transcript(messages, me), contexts, goals, topics,
                                           force=args.force)
                write_thread_extraction(driver, th["id"], x)
            except Exception as e:
                stats["failed"] += 1
                print(f"  [{i}/{len(todo)}] ✗ {th['subject'][:55]}: {type(e).__name__}: {str(e)[:100]}")
                continue
            stats["cached" if cached else "extracted"] += 1
            flags = " ".join(filter(None, ["REPLY" if x.needs_reply else "", x.urgency.upper() if x.urgency == "high"
                                           else "", f"→{x.context}" if x.context else ""]))
            print(f"  [{i}/{len(todo)}] {'·' if cached else '✓'} {th['subject'][:55]:<55} {x.category:<15} "
                  f"{len(x.commitments)} commitments {flags}")

        print(f"\n{stats['extracted']} extracted, {stats['cached']} from cache, {stats['failed']} failed")
        for r in driver.execute_query(SUMMARY, database_=database())[0]:
            print(f"  {r['category']:<16} {r['threads']:>3} threads · {r['need_reply']} need reply · "
                  f"{r['commitments']} commitments · {r['linked_to_memory']} linked to memory")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
