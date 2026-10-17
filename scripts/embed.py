"""Embed Topics and user Messages that don't have an embedding yet (local model, no API calls).

Run from the repo root after ingest/extract:  python -m scripts.embed
"""

import sys
import time

from mnemo.embed import embed_missing
from mnemo.graph.db import get_driver


def main() -> int:
    start = time.monotonic()
    with get_driver() as driver:
        done = embed_missing(driver)
    print(", ".join(f"{n} {label}s" for label, n in done.items()), f"embedded in {time.monotonic() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
