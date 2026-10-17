"""Check setup and initialise the graph.

Run from the repo root:  python -m scripts.init_db

1. Connects to Neo4j and applies mnemo/graph/schema.cypher (idempotent).
2. Checks every Groq key with a free models.list() call (no tokens used)
   and confirms the configured model IDs exist.
3. Checks the Gmail token works and how much email is in the graph.
"""

import os
import sys

from dotenv import load_dotenv
from groq import Groq

from mnemo.graph.db import apply_schema, database, get_driver


def check_neo4j() -> bool:
    print("Neo4j")
    try:
        driver = get_driver()
    except KeyError as e:
        print(f"  ✗ missing {e.args[0]} in .env")
        return False
    except Exception as e:
        print(f"  ✗ cannot connect: {e}")
        return False

    with driver:
        print(f"  ✓ connected to {os.environ['NEO4J_URI']}")
        n = apply_schema(driver)
        print(f"  ✓ applied {n} schema statements")
        records, _, _ = driver.execute_query(
            "SHOW INDEXES YIELD name, type, state RETURN name, type, state ORDER BY type, name",
            database_=database(),
        )
        not_online = [r["name"] for r in records if r["state"] != "ONLINE"]
        print(f"  ✓ {len(records)} indexes" + (f" (still building: {', '.join(not_online)})" if not_online else ""))
        records, _, _ = driver.execute_query("MATCH (d:Domain) RETURN count(d) AS n", database_=database())
        print(f"  ✓ {records[0]['n']} Domain nodes")
    return True


def check_groq() -> bool:
    print("Groq")
    keys = [k.strip() for k in os.environ.get("GROQ_API_KEYS", "").split(",") if k.strip()]
    if not keys:
        print("  ✗ GROQ_API_KEYS is empty")
        return False

    available: set[str] = set()
    ok = 0
    for i, key in enumerate(keys, 1):
        try:
            models = Groq(api_key=key, max_retries=0).models.list()
            available |= {m.id for m in models.data}
            ok += 1
            print(f"  ✓ key {i} (…{key[-4:]})")
        except Exception as e:
            print(f"  ✗ key {i} (…{key[-4:]}): {type(e).__name__}")

    wanted = [os.environ.get("GROQ_AGENT_MODEL", ""), os.environ.get("GROQ_EXTRACT_MODEL", "")]
    wanted += [m.strip() for m in os.environ.get("GROQ_FALLBACK_MODELS", "").split(",") if m.strip()]
    for m in filter(None, wanted):
        print(f"  {'✓' if m in available else '✗ not found:'} model {m}")
    return ok > 0 and all(m in available for m in filter(None, wanted))


def check_gmail() -> bool:
    print("Gmail")
    try:
        from mnemo.gmail.client import get_service, my_addresses

        print(f"  ✓ authorised as {', '.join(my_addresses(get_service()))}")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e} (run: python -m scripts.gmail_auth)")
        return False
    with get_driver() as driver:
        r = driver.execute_query("MATCH (t:Thread) RETURN count(t) AS threads, "
                                 "count(t.extracted_at) AS extracted", database_=database())[0][0]
    print(f"  ✓ {r['threads']} threads in graph, {r['extracted']} extracted")
    return True


def main() -> int:
    load_dotenv()
    results = [check_neo4j(), check_groq(), check_gmail()]
    print("\nAll good." if all(results) else "\nFix the ✗ items above, then re-run.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
