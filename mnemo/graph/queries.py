"""Read queries shared by extraction and (later) recall."""

from neo4j import Driver

from mnemo.graph.db import database

CONVERSATIONS_FOR_EXTRACTION = """
MATCH (c:Conversation)
WHERE $source IS NULL OR c.source = $source
OPTIONAL MATCH (c)-[:IN_CONTEXT]->(ctx:Context)
RETURN c.id AS id, c.title AS title, c.tool_calls AS tool_calls, c.tool_errors AS tool_errors,
       c.files_touched AS files, ctx.name AS ctx_name,
       [(c)-[:HAS_MESSAGE]->(m) | m {.role, .text, .idx}] AS messages
ORDER BY c.started_at
"""

EXISTING_GOALS = """
MATCH (:Me {id: 'me'})-[:HAS_GOAL]->(g:Goal)
RETURN g.id AS id, g.text AS text
"""

ACTIVE_PREFERENCES = """
MATCH (:Me {id: 'me'})-[r:PREFERS]->(p:Preference)
WHERE r.until IS NULL
RETURN p.id AS id, p.text AS text
ORDER BY r.since DESC
LIMIT 40
"""


TOP_TOPICS = """
MATCH (t:Topic)<-[:ABOUT]-(c:Conversation)
RETURN t.name AS name, count(c) AS n
ORDER BY n DESC, name
LIMIT 120
"""


def _rows(driver: Driver, query: str, **params) -> list[dict]:
    records, _, _ = driver.execute_query(query, params, database_=database())
    return [r.data() for r in records]


def conversations_for_extraction(driver: Driver, source: str | None = None) -> list[dict]:
    """Oldest first, so preferences get the right `since` and later ones can supersede earlier ones."""
    return _rows(driver, CONVERSATIONS_FOR_EXTRACTION, source=source)


def existing_goals(driver: Driver) -> list[dict]:
    return _rows(driver, EXISTING_GOALS)


def active_preferences(driver: Driver) -> list[dict]:
    return _rows(driver, ACTIVE_PREFERENCES)


def top_topics(driver: Driver) -> list[str]:
    """Most-used topic names, offered to the extractor so it reuses them instead of inventing synonyms."""
    return [r["name"] for r in _rows(driver, TOP_TOPICS)]
