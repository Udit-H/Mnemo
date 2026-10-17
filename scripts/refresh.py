"""Refresh everything before a demo: new email, new Claude Code sessions, extraction, embeddings.

Run from the repo root:  python -m scripts.refresh [--email-limit 40]
Each step is the normal script; cached work is skipped, so re-running is cheap.
"""

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email-limit", default="40")
    args = parser.parse_args()
    steps = [
        ["scripts.gmail_sync"],
        ["scripts.email_extract", "--limit", args.email_limit],
        ["scripts.ingest", "claude_code"],
        ["scripts.extract", "--source", "claude_code"],
        ["scripts.embed"],
    ]
    for step in steps:
        print(f"\n=== {' '.join(step)} ===", flush=True)
        if subprocess.call([sys.executable, "-m", *step]) != 0:
            print(f"✗ {step[0]} reported a problem (continuing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
