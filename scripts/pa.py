"""Chat with the Mnemo PA in the terminal (Gmail + graph tools).

Run from the repo root:  python -m scripts.pa [--trace]
"""

import argparse
import sys

from dotenv import load_dotenv

from mnemo.agent.pa_agent import PAAgent
from mnemo.agent.tools import ToolContext
from mnemo.gmail.client import get_service, my_addresses
from mnemo.graph.db import get_driver
from mnemo.llm import GroqPool


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", action="store_true", help="print each tool call's Cypher")
    args = parser.parse_args()

    load_dotenv()
    gmail = get_service()
    with get_driver() as driver:
        agent = PAAgent(ToolContext(driver=driver, llm=GroqPool.from_env(), gmail=gmail,
                                    self_emails=my_addresses(gmail)))
        print("Mnemo PA. Type 'exit' to quit.\n")
        while True:
            try:
                text = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ("exit", "quit"):
                break
            if not text:
                continue
            turn = agent.chat(text)
            for s in turn.steps:
                print(f"  ⚙ {s.tool}({s.args})")
                if args.trace and s.cypher:
                    print("    " + s.cypher.strip().replace("\n", "\n    "))
            print(f"\nmnemo › {turn.answer}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
