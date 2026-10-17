"""Extract memory (topics, entities, goals, tasks, preferences) from conversations already in the graph.

Run from the repo root:
    python -m scripts.extract                    # every conversation; cached ones cost no LLM calls
    python -m scripts.extract --source chatgpt   # one source only
    python -m scripts.extract --limit 40         # only the 40 most recent (still processed oldest first)
    python -m scripts.extract --force            # ignore the cache
    python -m scripts.extract --reset --force    # wipe all extracted memory first, then rebuild from scratch
"""

import argparse
import sys
from collections import Counter

from mnemo.extract.extractor import extract
from mnemo.graph.db import database, get_driver
from mnemo.graph.queries import active_preferences, conversations_for_extraction, existing_goals, top_topics
from mnemo.graph.writer import link_topics, write_extraction
from mnemo.llm import GroqPool

SUMMARY = """
CALL () {
  MATCH (n) WHERE n:Topic OR n:Entity OR n:Goal OR n:Task OR n:Preference OR n:Correction
  RETURN labels(n)[0] AS k, count(*) AS n
  UNION ALL
  MATCH ()-[r:RELATED_TO|SUPERSEDES|SERVES]->() RETURN type(r) AS k, count(*) AS n
}
RETURN k, n ORDER BY k
"""

# Leaves ingested data (Me, Domain, Context, Conversation, Message) untouched.
RESET = """
MATCH (n) WHERE n:Topic OR n:Entity OR n:Goal OR n:Task OR n:Outcome OR n:Preference OR n:Correction
DETACH DELETE n
WITH count(*) AS _
MATCH (c:Conversation)-[r:IN_DOMAIN]->()
DELETE r
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["claude_code", "chatgpt", "claude_ai", "gemini"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    llm = GroqPool.from_env()
    stats = Counter()
    with get_driver() as driver:
        if args.reset:
            driver.execute_query(RESET, database_=database())
            print("Reset: removed all extracted memory")
        convs = conversations_for_extraction(driver, args.source)
        if args.limit:
            convs = convs[-args.limit:]
        print(f"Extracting {len(convs)} conversations with {llm.extract_model}")
        for i, conv in enumerate(convs, 1):
            # Re-read memory each time so later conversations can reuse or supersede earlier results.
            # One failure (rate limit, bad JSON, network blip) skips this conversation; re-run to resume.
            try:
                goals, prefs, topics = existing_goals(driver), active_preferences(driver), top_topics(driver)
                x, cached = extract(llm, conv, goals, prefs, topics, force=args.force)
                write_extraction(driver, conv["id"], x, {g["id"] for g in goals}, {p["id"] for p in prefs})
            except Exception as e:
                stats["failed"] += 1
                print(f"  [{i}/{len(convs)}] ✗ {conv['title'][:60]}: {type(e).__name__}: {str(e)[:120]}")
                continue
            stats["cached" if cached else "extracted"] += 1
            print(f"  [{i}/{len(convs)}] {'·' if cached else '✓'} {conv['title'][:60]}  "
                  f"({x.domain}; {len(x.topics)} topics, {len(x.tasks)} tasks, {len(x.preferences)} prefs)")

        link_topics(driver)
        print(f"\n{stats['extracted']} extracted, {stats['cached']} from cache, {stats['failed']} failed")
        for r in driver.execute_query(SUMMARY, database_=database())[0]:
            print(f"  {r['k']:<12}{r['n']}")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
