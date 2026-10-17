"""Canned Cypher for the Insights tab. Each entry: (title, explanation, query). Shown with its Cypher in the UI."""

INSIGHTS = [
    (
        "Cross-domain bridges",
        "Topics and tools that connect different areas of your life. These links exist only because the memory "
        "is a graph.",
        """MATCH (n)<-[:ABOUT|MENTIONS]-(c:Conversation)-[:IN_DOMAIN]->(d:Domain)
WHERE n:Topic OR n:Entity
WITH n, collect(DISTINCT d.name) AS domains, collect(DISTINCT c.source) AS sources,
     count(DISTINCT c) AS conversations
WHERE size(domains) > 1
RETURN n.name AS name, labels(n)[0] AS type, domains, sources, conversations
ORDER BY size(domains) DESC, conversations DESC
LIMIT 15""",
    ),
    (
        "Goals and progress",
        "Each goal, the tasks that served it, and how they turned out.",
        """MATCH (:Me {id: 'me'})-[:HAS_GOAL]->(g:Goal)
OPTIONAL MATCH (g)<-[:SERVES]-(t:Task)
WITH g, count(t) AS tasks,
     sum(CASE WHEN t.status = 'success' THEN 1 ELSE 0 END) AS succeeded,
     sum(CASE WHEN t.status IN ['failed', 'abandoned'] THEN 1 ELSE 0 END) AS failed,
     max(t.ts) AS last
RETURN g.text AS goal, [(g)-[:IN_DOMAIN]->(d) | d.name] AS domains,
       tasks, succeeded, failed, toString(date(last)) AS last_activity
ORDER BY last_activity IS NULL, last_activity DESC""",
    ),
    (
        "Where you got stuck",
        "Tasks that failed or were abandoned, with what went wrong and the tools involved.",
        """MATCH (t:Task)-[:FROM]->(c:Conversation)
WHERE t.status IN ['failed', 'abandoned']
OPTIONAL MATCH (t)-[:LED_TO]->(o:Outcome)
RETURN toString(date(c.started_at)) AS date, t.text AS task, o.summary AS what_happened,
       [(t)-[:USES]->(e) | e.name] AS involved, c.source AS source
ORDER BY date DESC""",
    ),
    (
        "Preferences over time",
        "What you prefer, where it applies, and which preferences replaced older ones (temporal memory).",
        """MATCH (:Me {id: 'me'})-[r:PREFERS]->(p:Preference)
OPTIONAL MATCH (p)-[:APPLIES_IN]->(s)
RETURN p.text AS preference, collect(coalesce(s.name, s.id)) AS applies_in,
       toString(date(r.since)) AS since,
       CASE WHEN r.until IS NULL THEN 'active' ELSE 'replaced ' + toString(date(r.until)) END AS status,
       [(p)-[:SUPERSEDES]->(old) | old.text] AS replaced
ORDER BY since DESC""",
    ),
    (
        "Most connected topics",
        "Topics that come up together across conversations (RELATED_TO, weighted by shared conversations).",
        """MATCH (a:Topic)-[r:RELATED_TO]-(b:Topic)
WHERE elementId(a) < elementId(b)
RETURN a.name AS topic, b.name AS related_to, r.weight AS shared_conversations
ORDER BY r.weight DESC
LIMIT 15""",
    ),
]

DOMAIN_TIMELINE = """
MATCH (c:Conversation)-[:IN_DOMAIN]->(d:Domain)
WHERE c.started_at IS NOT NULL
RETURN substring(toString(c.started_at), 0, 7) AS month, d.name AS domain, count(c) AS conversations
ORDER BY month
"""

GRAPH_STATS = """
CALL () {
  MATCH (c:Conversation) RETURN 'conversations · ' + c.source AS k, count(*) AS n
  UNION ALL
  MATCH (n) WHERE n:Topic OR n:Entity OR n:Goal OR n:Task OR n:Preference
  RETURN labels(n)[0] AS k, count(*) AS n
  UNION ALL
  MATCH ()-[r]->() RETURN 'relationships' AS k, count(r) AS n
}
RETURN k, n ORDER BY k
"""
