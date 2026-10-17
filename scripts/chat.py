"""Chat with Mnemo in the terminal.

Run from the repo root:  python -m scripts.chat [--no-remember] [--show-memory]
                         python -m scripts.chat --forget    # delete all live chat sessions (e.g. after rehearsing)
Type 'exit' to quit.
"""

import argparse
import sys

from mnemo.agent.agent import Agent
from mnemo.graph.db import get_driver
from mnemo.graph.writer import forget_source
from mnemo.llm import GroqPool


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-remember", action="store_true", help="don't write this session back to the graph")
    parser.add_argument("--show-memory", action="store_true", help="print the recalled memory block each turn")
    parser.add_argument("--forget", action="store_true", help="delete all live sessions and what they taught Mnemo")
    args = parser.parse_args()

    with get_driver() as driver:
        if args.forget:
            print(f"Forgot all live sessions ({forget_source(driver, 'live')} nodes deleted)")
            return 0
        agent = Agent(driver=driver, llm=GroqPool.from_env())
        print(f"Mnemo (session {agent.session_id}). Type 'exit' to quit.\n")
        while True:
            try:
                text = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ("exit", "quit"):
                break
            if not text:
                continue
            turn = agent.chat(text, remember=not args.no_remember)
            r = turn.recall
            print(f"\n  ⟡ recalled {len(r.conversations)} conversations, {len(r.goals)} goals, "
                  f"{len(r.preferences)} preferences ({len(r.nodes)} nodes, {len(r.edges)} edges)")
            if args.show_memory:
                print("  " + r.context.replace("\n", "\n  "))
            print(f"\nmnemo › {turn.answer}\n")
            if turn.remembered:
                x = turn.remembered
                new = [f"pref: {p.text}" for p in x.preferences] + [f"goal: {g.text}" for g in x.goals]
                print(f"  ⟡ remembered: {'; '.join(new) or ', '.join(x.topics) or 'nothing new'}\n")
            elif turn.memory_error:
                print(f"  ⟡ memory not saved: {turn.memory_error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
