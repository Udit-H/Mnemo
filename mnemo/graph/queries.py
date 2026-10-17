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


# --- Recall (M3) -------------------------------------------------------------

# Seeds: memory nodes whose names match words in the user's message.
SEED_FULLTEXT = """
CALL db.index.fulltext.queryNodes('memory_names', $q) YIELD node, score
RETURN elementId(node) AS id, labels(node)[0] AS label, coalesce(node.name, node.text) AS name, score
LIMIT 10
"""

# Seeds: topics whose meaning is close to the message.
SEED_TOPIC_VECTOR = """
CALL db.index.vector.queryNodes('topic_embedding', 6, $vec) YIELD node, score
WHERE score >= $min_score
RETURN elementId(node) AS id, 'Topic' AS label, node.name AS name, score
"""

# Conversations containing a past user message close in meaning to this one.
MESSAGE_VECTOR_CONVERSATIONS = """
CALL db.index.vector.queryNodes('message_embedding', 12, $vec) YIELD node, score
WHERE score >= $min_score
MATCH (c:Conversation)-[:HAS_MESSAGE]->(node)
WHERE NOT c.id IN $exclude
RETURN c.id AS conv_id, max(score) AS score
"""

# Seed → the conversations it reaches in one hop, whatever kind of seed it is.
SEED_CONVERSATIONS = """
UNWIND $seeds AS s
MATCH (n) WHERE elementId(n) = s.id
CALL (n) {
  MATCH (n)<-[:ABOUT|MENTIONS]-(c:Conversation) RETURN c
  UNION
  MATCH (n)<-[:IN_CONTEXT]-(c:Conversation) RETURN c
  UNION
  MATCH (n)-[:FROM]->(c:Conversation) RETURN c
  UNION
  MATCH (n)<-[:USES]-(:Task)-[:FROM]->(c:Conversation) RETURN c
}
WITH c, s WHERE NOT c.id IN $exclude
RETURN c.id AS conv_id, c.started_at AS started_at, sum(s.score) AS score, collect(DISTINCT s.id) AS via
"""

CONVERSATION_DETAILS = """
UNWIND $conv_ids AS cid
MATCH (c:Conversation {id: cid})
OPTIONAL MATCH (c)-[:IN_DOMAIN]->(d:Domain)
OPTIONAL MATCH (c)-[:IN_CONTEXT]->(ctx:Context)
RETURN c.id AS id, c.title AS title, c.summary AS summary, c.source AS source,
       toString(date(c.started_at)) AS date, d.name AS domain, ctx.name AS context,
       [(c)-[:ABOUT]->(t:Topic) | t.name] AS topics,
       [(t:Task)-[:FROM]->(c) | {
          id: t.id, text: t.text, outcome: t.status,
          summary: [(t)-[:LED_TO]->(o) | o.summary][0],
          goal_id: [(t)-[:SERVES]->(g) | g.id][0]
       }] AS tasks
"""

# Active preferences that apply where these conversations happened, plus global ones and any matched directly.
RELEVANT_PREFERENCES = """
MATCH (:Me {id: 'me'})-[r:PREFERS]->(p:Preference)
WHERE r.until IS NULL
OPTIONAL MATCH (p)-[:APPLIES_IN]->(scope)
WITH p, r, collect(coalesce(scope.name, scope.id)) AS scopes
WHERE size(scopes) = 0 OR any(x IN scopes WHERE x IN $scopes) OR elementId(p) IN $seed_ids
RETURN p.id AS id, p.text AS text, scopes, toString(date(r.since)) AS since,
       [(p)-[:SUPERSEDES]->(old) | old.text] AS replaced
ORDER BY r.since DESC
LIMIT 12
"""

RELEVANT_GOALS = """
MATCH (:Me {id: 'me'})-[:HAS_GOAL]->(g:Goal)
WHERE elementId(g) IN $seed_ids
   OR EXISTS { (g)<-[:SERVES]-(:Task)-[:FROM]->(c:Conversation) WHERE c.id IN $conv_ids }
   OR EXISTS { (g)-[:FROM]->(c:Conversation) WHERE c.id IN $conv_ids }
RETURN g.id AS id, g.text AS text, g.status AS status, [(g)-[:IN_DOMAIN]->(d) | d.name] AS domains
LIMIT 8
"""


def recall_rows(driver: Driver, query: str, **params) -> list[dict]:
    return _rows(driver, query, **params)
