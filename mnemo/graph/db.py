"""Neo4j driver and schema setup. Connection settings come from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

SCHEMA_PATH = Path(__file__).with_name("schema.cypher")


def get_driver() -> Driver:
    load_dotenv()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
        # Silence "relationship type does not exist yet" warnings on a fresh graph, and the
        # db.index.vector.queryNodes → SEARCH deprecation notice (queryNodes still works on Aura).
        notifications_disabled_classifications=["UNRECOGNIZED", "DEPRECATION"],
    )
    driver.verify_connectivity()
    return driver


def database() -> str:
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def _schema_statements() -> list[str]:
    text = SCHEMA_PATH.read_text().replace("__EMBEDDING_DIM__", os.environ.get("EMBEDDING_DIM", "384"))
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    return [s.strip() for s in code.split(";") if s.strip()]


def apply_schema(driver: Driver) -> int:
    """Create constraints, indexes and seed nodes. Returns the number of statements run."""
    statements = _schema_statements()
    for stmt in statements:
        driver.execute_query(stmt, database_=database())
    return len(statements)
