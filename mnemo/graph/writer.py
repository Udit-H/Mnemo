"""Idempotent writes of normalized conversations into Neo4j. Re-running never duplicates nodes."""

from neo4j import Driver, ManagedTransaction

from mnemo.extract.schema import Extraction, norm, stable_id
from mnemo.graph.db import database
from mnemo.ingest.models import Conversation

MAX_FILES_TOUCHED = 50

WRITE_CONVERSATION = """
MERGE (c:Conversation {id: $conv.id})
SET c.source = $conv.source, c.title = $conv.title,
    c.started_at = $conv.started_at, c.ended_at = $conv.ended_at,
    c.message_count = size($conv.messages), c.tool_calls = $tool_calls, c.tool_errors = $tool_errors,
    c.tools_used = $tools_used, c.files_touched = $files_touched
WITH c
UNWIND range(0, size($conv.messages) - 1) AS i
WITH c, i, $conv.messages[i] AS m
MERGE (msg:Message {id: m.id})
SET msg.role = m.role, msg.text = m.text, msg.ts = m.ts, msg.idx = i
MERGE (c)-[:HAS_MESSAGE]->(msg)
"""

WRITE_CONTEXT = """
MATCH (c:Conversation {id: $conv_id})
MERGE (ctx:Context {id: $ctx.id})
SET ctx.name = $ctx.name, ctx.kind = $ctx.kind
MERGE (c)-[:IN_CONTEXT]->(ctx)
WITH ctx
MATCH (me:Me {id: 'me'})
MERGE (me)-[:WORKS_ON]->(ctx)
WITH ctx
OPTIONAL MATCH (d:Domain {name: $ctx.domain})
FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (ctx)-[:IN_DOMAIN]->(d))
"""

# Rebuilt from scratch per context so late-arriving conversations slot into the right place.
LINK_SEQUENCE = """
MATCH (ctx:Context {id: $ctx_id})<-[:IN_CONTEXT]-(c:Conversation)
OPTIONAL MATCH (c)-[old:FOLLOWED_BY]->()
DELETE old
WITH DISTINCT ctx, c ORDER BY c.started_at
WITH collect(c) AS convs
UNWIND range(0, size(convs) - 2) AS i
WITH convs[i] AS a, convs[i + 1] AS b
MERGE (a)-[:FOLLOWED_BY]->(b)
"""


def _write_one(tx: ManagedTransaction, conv: Conversation) -> None:
    files = list(dict.fromkeys(
        t.target for t in conv.tool_calls if t.target and t.name in ("Read", "Edit", "Write", "NotebookEdit")
    ))
    tx.run(
        WRITE_CONVERSATION,
        conv=conv.model_dump(),
        tool_calls=len(conv.tool_calls),
        tool_errors=sum(t.is_error for t in conv.tool_calls),
        tools_used=sorted({t.name for t in conv.tool_calls}),
        files_touched=files[:MAX_FILES_TOUCHED],
    )
    if conv.context:
        tx.run(WRITE_CONTEXT, conv_id=conv.id, ctx=conv.context.model_dump())


def write_conversations(driver: Driver, conversations: list[Conversation]) -> None:
    """One transaction per conversation, then relink FOLLOWED_BY chains for the touched contexts."""
    with driver.session(database=database()) as session:
        for conv in conversations:
            session.execute_write(_write_one, conv)
        for ctx_id in {c.context.id for c in conversations if c.context}:
            session.execute_write(lambda tx: tx.run(LINK_SEQUENCE, ctx_id=ctx_id).consume())


# --- Extraction (M2) ---------------------------------------------------------

# Re-extracting a conversation replaces what it contributed; shared Goal/Preference nodes are kept.
CLEAR_EXTRACTION = """
MATCH (c:Conversation {id: $id})
OPTIONAL MATCH (c)-[r:ABOUT|MENTIONS|IN_DOMAIN]->()
DELETE r
WITH DISTINCT c
OPTIONAL MATCH (x)-[:FROM|IN]->(c) WHERE x:Task OR x:Correction
OPTIONAL MATCH (x)-[:LED_TO]->(o:Outcome)
DETACH DELETE x, o
WITH DISTINCT c
OPTIONAL MATCH (g)-[f:FROM]->(c) WHERE g:Goal OR g:Preference
DELETE f
"""

WRITE_SUMMARY = """
MATCH (c:Conversation {id: $id}), (d:Domain {name: $x.domain})
SET c.summary = $x.summary, c.extracted_at = datetime()
MERGE (c)-[:IN_DOMAIN]->(d)
FOREACH (t IN $x.topics |
  MERGE (tp:Topic {name: t})
  MERGE (c)-[:ABOUT]->(tp)
  MERGE (tp)-[:IN_DOMAIN]->(d))
FOREACH (e IN $x.entities |
  MERGE (en:Entity {name: e.name}) ON CREATE SET en.kind = e.kind
  MERGE (c)-[:MENTIONS]->(en))
"""

WRITE_GOALS = """
MATCH (c:Conversation {id: $id}), (me:Me {id: 'me'}), (d:Domain {name: $domain})
UNWIND $goals AS g
MERGE (goal:Goal {id: g.id}) ON CREATE SET goal.text = g.text, goal.status = 'active'
MERGE (me)-[:HAS_GOAL]->(goal)
MERGE (goal)-[:IN_DOMAIN]->(d)
MERGE (goal)-[:FROM]->(c)
"""

WRITE_TASKS = """
MATCH (c:Conversation {id: $id})
UNWIND $tasks AS t
MERGE (task:Task {id: t.id})
SET task.text = t.text, task.status = t.outcome, task.ts = c.started_at
MERGE (task)-[:FROM]->(c)
MERGE (o:Outcome {id: t.id + ':outcome'})
SET o.kind = t.outcome, o.summary = t.outcome_summary
MERGE (task)-[:LED_TO]->(o)
FOREACH (name IN t.uses |
  MERGE (e:Entity {name: name}) ON CREATE SET e.kind = 'other'
  MERGE (task)-[:USES]->(e))
WITH task, t WHERE t.goal_id IS NOT NULL
MATCH (g:Goal {id: t.goal_id})
MERGE (task)-[:SERVES]->(g)
"""

WRITE_PREFERENCES = """
MATCH (c:Conversation {id: $id}), (me:Me {id: 'me'}), (d:Domain {name: $domain})
OPTIONAL MATCH (c)-[:IN_CONTEXT]->(ctx:Context)
UNWIND $prefs AS p
MERGE (pref:Preference {id: p.id}) ON CREATE SET pref.text = p.text
MERGE (pref)-[:FROM]->(c)
MERGE (me)-[r:PREFERS]->(pref) ON CREATE SET r.since = c.started_at
SET r.mentions = COUNT { (pref)-[:FROM]->() }
FOREACH (_ IN CASE WHEN p.scope = 'domain' OR (p.scope = 'context' AND ctx IS NULL) THEN [1] ELSE [] END |
  MERGE (pref)-[:APPLIES_IN]->(d))
FOREACH (_ IN CASE WHEN p.scope = 'context' AND ctx IS NOT NULL THEN [1] ELSE [] END |
  MERGE (pref)-[:APPLIES_IN]->(ctx))
FOREACH (_ IN CASE WHEN p.correction IS NULL THEN [] ELSE [1] END |
  MERGE (corr:Correction {id: p.id + '@' + $id})
  SET corr.text = p.correction, corr.ts = c.started_at
  MERGE (corr)-[:IN]->(c)
  MERGE (corr)-[:UPDATED]->(pref))
WITH me, pref, p, c
OPTIONAL MATCH (me)-[old:PREFERS]->(oldp:Preference {id: p.replaces})
WHERE old.until IS NULL AND oldp <> pref
SET old.until = c.started_at
FOREACH (_ IN CASE WHEN oldp IS NULL THEN [] ELSE [1] END | MERGE (pref)-[:SUPERSEDES]->(oldp))
"""

# Topics discussed together in 2+ conversations are related; weight = shared conversations.
LINK_TOPICS = """
MATCH (a:Topic)<-[:ABOUT]-(c:Conversation)-[:ABOUT]->(b:Topic)
WHERE elementId(a) < elementId(b)
WITH a, b, count(DISTINCT c) AS w
WHERE w >= 2
MERGE (a)-[r:RELATED_TO]->(b)
SET r.weight = w
"""


def _resolve(conv_id: str, x: Extraction, goal_ids: set[str], pref_ids: set[str]) -> dict:
    """Turn LLM output into graph params, reusing existing ids only when they really exist."""
    goal_by_text = {}
    goals = []
    for g in x.goals:
        gid = g.existing_id if g.existing_id in goal_ids else stable_id("goal", g.text)
        goal_by_text[norm(g.text)] = gid
        goals.append({"id": gid, "text": g.text})

    def goal_id(ref: str | None) -> str | None:
        if not ref:
            return None
        return ref if ref in goal_ids else goal_by_text.get(norm(ref))

    tasks = [{
        "id": stable_id("task", conv_id, t.text), "text": t.text, "outcome": t.outcome,
        "outcome_summary": t.outcome_summary, "uses": t.uses, "goal_id": goal_id(t.goal),
    } for t in x.tasks]

    prefs = [{
        "id": p.existing_id if p.existing_id in pref_ids else stable_id("pref", p.text),
        "text": p.text, "scope": p.scope, "correction": p.correction,
        "replaces": p.replaces if p.replaces in pref_ids else None,
    } for p in x.preferences]

    return {"x": x.model_dump(include={"domain", "summary", "topics", "entities"}),
            "goals": goals, "tasks": tasks, "prefs": prefs}


def write_extraction(driver: Driver, conv_id: str, x: Extraction, goal_ids: set[str], pref_ids: set[str]) -> None:
    params = _resolve(conv_id, x, goal_ids, pref_ids)

    def work(tx: ManagedTransaction) -> None:
        tx.run(CLEAR_EXTRACTION, id=conv_id).consume()
        tx.run(WRITE_SUMMARY, id=conv_id, x=params["x"]).consume()
        tx.run(WRITE_GOALS, id=conv_id, domain=x.domain, goals=params["goals"]).consume()
        tx.run(WRITE_TASKS, id=conv_id, tasks=params["tasks"]).consume()
        tx.run(WRITE_PREFERENCES, id=conv_id, domain=x.domain, prefs=params["prefs"]).consume()

    with driver.session(database=database()) as session:
        session.execute_write(work)


# Nodes left behind when re-extraction removed the only thing that referenced them.
PRUNE_ORPHANS = """
MATCH (n)
WHERE (n:Topic AND NOT (n)<-[:ABOUT]-())
   OR (n:Entity AND NOT (n)<-[:MENTIONS|USES]-())
   OR ((n:Goal OR n:Preference) AND NOT (n)-[:FROM]->())
DETACH DELETE n
"""


FORGET_SOURCE = """
MATCH (c:Conversation {source: $source})
OPTIONAL MATCH (x)-[:FROM|IN]->(c) WHERE x:Task OR x:Correction
OPTIONAL MATCH (x)-[:LED_TO]->(o:Outcome)
DETACH DELETE x, o
WITH DISTINCT c
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m)
DETACH DELETE c, m
"""


def forget_source(driver: Driver, source: str) -> int:
    """Delete every conversation from one source and the memory only it contributed. Returns nodes deleted."""
    deleted = driver.execute_query(FORGET_SOURCE, source=source, database_=database()).summary.counters.nodes_deleted
    link_topics(driver)
    return deleted


def link_topics(driver: Driver) -> None:
    driver.execute_query(PRUNE_ORPHANS, database_=database())
    driver.execute_query(LINK_TOPICS, database_=database())
