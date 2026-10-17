"""Back up the whole graph to JSON, or restore it into an empty Neo4j (e.g. local Docker if Aura/wifi fails).

Run from the repo root:
    python -m scripts.backup export                         # → data/backup/graph-<timestamp>.json
    python -m scripts.backup restore data/backup/graph-….json --uri bolt://localhost:7687 --password password123
Local fallback DB:  docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:5
Restore needs Neo4j 5.26+ (dynamic labels) and refuses a non-empty database unless --force.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.time import Date, DateTime

from mnemo.graph.db import apply_schema, get_driver

BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "backup"
BATCH = 500


def _encode(value):
    if isinstance(value, DateTime):
        return {"__dt": value.to_native().isoformat()}
    if isinstance(value, Date):
        return {"__date": value.to_native().isoformat()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _decode(value):
    if isinstance(value, dict) and "__dt" in value:
        return datetime.fromisoformat(value["__dt"])
    if isinstance(value, dict) and "__date" in value:
        return date.fromisoformat(value["__date"])
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def export(driver) -> Path:
    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
        nodes = [{"id": r["id"], "labels": r["labels"], "props": {k: _encode(v) for k, v in r["props"].items()}}
                 for r in s.run("MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props")]
        rels = [{"s": r["s"], "t": r["t"], "type": r["type"], "props": {k: _encode(v) for k, v in r["props"].items()}}
                for r in s.run("MATCH (a)-[r]->(b) RETURN elementId(a) AS s, elementId(b) AS t, type(r) AS type, "
                               "properties(r) AS props")]
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"graph-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"nodes": nodes, "rels": rels}))
    print(f"Exported {len(nodes)} nodes, {len(rels)} relationships → {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def restore(driver, path: Path, force: bool) -> None:
    data = json.loads(path.read_text())
    db = os.environ.get("NEO4J_DATABASE", "neo4j")
    existing = driver.execute_query("MATCH (n) RETURN count(n) AS n", database_=db)[0][0]["n"]
    if existing and not force:
        sys.exit(f"Target database has {existing} nodes. Use an empty database, or --force to add anyway.")
    apply_schema(driver)
    driver.execute_query("CREATE INDEX restore_bid IF NOT EXISTS FOR (n:_Restore) ON (n._bid)", database_=db)

    nodes = [{"id": n["id"], "labels": n["labels"], "props": {k: _decode(v) for k, v in n["props"].items()}}
             for n in data["nodes"]]
    for i in range(0, len(nodes), BATCH):
        driver.execute_query("""UNWIND $rows AS r
            CREATE (n:_Restore:$(r.labels)) SET n = r.props, n._bid = r.id""", rows=nodes[i:i + BATCH], database_=db)
    rels = [{**r, "props": {k: _decode(v) for k, v in r["props"].items()}} for r in data["rels"]]
    for i in range(0, len(rels), BATCH):
        driver.execute_query("""UNWIND $rows AS r
            MATCH (a:_Restore {_bid: r.s}), (b:_Restore {_bid: r.t})
            CREATE (a)-[x:$(r.type)]->(b) SET x = r.props""", rows=rels[i:i + BATCH], database_=db)
    driver.execute_query("MATCH (n:_Restore) REMOVE n:_Restore, n._bid", database_=db)
    driver.execute_query("DROP INDEX restore_bid IF EXISTS", database_=db)
    print(f"Restored {len(nodes)} nodes, {len(rels)} relationships from {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["export", "restore"])
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--uri")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    if args.uri:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
    else:
        driver = get_driver()
    with driver:
        if args.action == "export":
            export(driver)
        else:
            if not args.path:
                parser.error("restore needs the backup file path")
            restore(driver, args.path, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
