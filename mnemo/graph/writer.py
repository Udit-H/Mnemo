"""Idempotent writes of normalized conversations into Neo4j. Re-running never duplicates nodes."""

from neo4j import Driver, ManagedTransaction

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
