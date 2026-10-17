"""Load conversations into the graph.

Run from the repo root:
    python -m scripts.ingest claude_code                       # ~/.claude/projects
    python -m scripts.ingest chatgpt   data/chatgpt/conversations.json
    python -m scripts.ingest claude_ai data/claude_ai/conversations.json
Options:
    --limit N    only the N most recent conversations (saves Groq quota later in extraction)
    --dry-run    parse and summarise without writing to Neo4j
"""

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mnemo.graph.db import database, get_driver
from mnemo.graph.writer import write_conversations
from mnemo.ingest import chatgpt, claude_ai, claude_code

PARSERS = {"claude_code": claude_code.parse_all, "chatgpt": chatgpt.parse_all, "claude_ai": claude_ai.parse_all}
EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", choices=PARSERS)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.source != "claude_code" and not args.path:
        parser.error(f"{args.source} needs the path to conversations.json")
    convs = PARSERS[args.source](args.path) if args.path else PARSERS[args.source]()
    convs.sort(key=lambda c: c.started_at or EPOCH, reverse=True)
    if args.limit:
        convs = convs[: args.limit]

    messages = sum(len(c.messages) for c in convs)
    contexts = Counter(c.context.name for c in convs if c.context)
    print(f"Parsed {len(convs)} conversations, {messages} messages from {args.source}")
    for name, n in contexts.most_common():
        print(f"  {n:>3}  {name}")

    if args.dry_run or not convs:
        return 0

    with get_driver() as driver:
        write_conversations(driver, convs)
        records, _, _ = driver.execute_query(
            "MATCH (c:Conversation {source: $source}) RETURN count(c) AS convs",
            source=args.source, database_=database(),
        )
    print(f"Wrote to Neo4j. Graph now has {records[0]['convs']} {args.source} conversations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
