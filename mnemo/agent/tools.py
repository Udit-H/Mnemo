"""PA agent tools. Each returns (result, cypher) so the UI can show exactly what the graph was asked."""

import json
from dataclasses import dataclass
from typing import Any, Callable

from neo4j import Driver, READ_ACCESS

from mnemo.agent.recall import recall
from mnemo.gmail.client import create_draft, fetch_thread_text
from mnemo.graph import pa as Q
from mnemo.graph.db import database
from mnemo.llm import GroqPool

RESULT_CHARS = 6000


@dataclass
class ToolContext:
    driver: Driver
    llm: GroqPool
    gmail: Any  # googleapiclient service
    self_emails: list[str]


def _rows(ctx: ToolContext, query: str, **params) -> list[dict]:
    records, _, _ = ctx.driver.execute_query(query, params, database_=database())
    return [r.data() for r in records]


def triage_inbox(ctx: ToolContext, limit: int = 8, days: int = 30):
    return _rows(ctx, Q.TRIAGE, limit=limit, days=days), Q.TRIAGE


def person_context(ctx: ToolContext, name_or_email: str):
    rows = _rows(ctx, Q.PERSON, q=name_or_email)
    return (rows[0] if rows else {"error": f"No person matching '{name_or_email}'"}), Q.PERSON


def open_commitments(ctx: ToolContext, owner: str = "any", limit: int = 15, overdue_days: int = 7):
    return _rows(ctx, Q.COMMITMENTS, owner=owner, limit=limit, overdue_days=overdue_days), Q.COMMITMENTS


def search_memory(ctx: ToolContext, query: str):
    """Email threads (fulltext) + long-term memory from past AI chats (graph recall)."""
    words = " OR ".join(w for w in query.replace('"', " ").split() if len(w) > 2) or query
    threads = _rows(ctx, Q.THREAD_SEARCH, q=words)
    mem = recall(ctx.driver, query)
    return {"email_threads": threads, "chat_memory": mem.context}, Q.THREAD_SEARCH


def thread_details(ctx: ToolContext, thread_id: str):
    graph = _rows(ctx, Q.THREAD_GRAPH, id=thread_id)
    messages = fetch_thread_text(ctx.gmail, thread_id)[-4:]
    return {
        "graph": graph[0] if graph else {},
        "messages": [{"from": m["from"].name or m["from"].email, "at": f"{m['at']:%Y-%m-%d %H:%M}",
                      "text": m["text"][:1500]} for m in messages],
    }, Q.THREAD_GRAPH


def compose_reply(ctx: ToolContext, thread_id: str, instructions: str = "") -> dict:
    """Write a reply in the user's voice for this person. Doesn't save anything (see save_draft)."""
    style = (_rows(ctx, Q.REPLY_CONTEXT, id=thread_id) or [{}])[0]
    messages = fetch_thread_text(ctx.gmail, thread_id)
    if not messages:
        return {"error": "thread not found"}
    last_other = next((m for m in reversed(messages) if m["from"].email not in ctx.self_emails), messages[-1])
    transcript = "\n\n---\n\n".join(f"FROM {m['from'].name or m['from'].email}:\n{m['text'][:1500]}"
                                    for m in messages[-4:])
    prompt = f"""Write the body of the user's email reply. Output only the body text, no subject line.
Recipient: {style.get('name') or last_other['from'].email} ({style.get('org') or 'personal address'})
How the user writes to this person/org (follow strictly): {style.get('scoped_preferences') or 'no specific notes'}
User's general writing preferences: {style.get('global_preferences') or 'none recorded'}
Examples of the user's past emails to them (match tone and length): {style.get('your_past_emails_to_them') or 'none'}
User's instructions for this reply: {instructions or 'reply appropriately'}

Thread:
{transcript}"""
    resp = ctx.llm.chat([{"role": "user", "content": prompt}], model=ctx.llm.agent_model, temperature=0.4)
    return {"thread_id": thread_id, "to": last_other["from"].email, "subject": messages[0]["subject"],
            "body": (resp.choices[0].message.content or "").strip(),
            "in_reply_to": last_other["message_id"], "references": last_other["references"],
            "style_used": {k: style.get(k) for k in ("scoped_preferences", "global_preferences")}}


def save_draft(ctx: ToolContext, reply: dict, body: str | None = None) -> str:
    """Save a composed reply (optionally with the user's edited body) to Gmail Drafts. Never sends."""
    return create_draft(ctx.gmail, reply["thread_id"], to=reply["to"], subject=reply["subject"],
                        body=body if body is not None else reply["body"], in_reply_to=reply["in_reply_to"],
                        references=reply["references"])


def draft_reply(ctx: ToolContext, thread_id: str, instructions: str = ""):
    """Write a reply in the user's voice for this person, and save it to Gmail Drafts (never sends)."""
    reply = compose_reply(ctx, thread_id, instructions)
    if "error" in reply:
        return reply, Q.REPLY_CONTEXT
    reply["draft_id"] = save_draft(ctx, reply)
    reply["note"] = "Saved to Gmail Drafts. Not sent."
    return reply, Q.REPLY_CONTEXT


def run_cypher(ctx: ToolContext, query: str):
    """Read-only Cypher for questions the other tools don't cover. Writes are rejected by the database."""
    with ctx.driver.session(database=database(), default_access_mode=READ_ACCESS) as session:
        rows = session.execute_read(lambda tx: [r.data() for r in tx.run(query)][:50])
    return rows, query


TOOLS: dict[str, Callable] = {f.__name__: f for f in (
    triage_inbox, person_context, open_commitments, search_memory, thread_details, draft_reply, run_cypher)}

SPECS = [
    {"type": "function", "function": {
        "name": "triage_inbox",
        "description": "Inbox threads ranked by what matters to the user (people they care about, needs reply, "
                       "urgency, what they owe, links to their projects). Use for 'what should I focus on'.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 8}, "days": {"type": "integer", "default": 30}}}}},
    {"type": "function", "function": {
        "name": "person_context",
        "description": "Everything about one person: org, how the user corresponds with them (reply rate, speed), "
                       "recent threads, open commitments both ways, how the user writes to them.",
        "parameters": {"type": "object", "properties": {"name_or_email": {"type": "string"}},
                       "required": ["name_or_email"]}}},
    {"type": "function", "function": {
        "name": "open_commitments",
        "description": "Open promises/requests from email with due dates. owner: 'me' (user owes), 'them' "
                       "(others owe the user) or 'any'.",
        "parameters": {"type": "object", "properties": {
            "owner": {"type": "string", "enum": ["me", "them", "any"], "default": "any"},
            "limit": {"type": "integer", "default": 15}}}}},
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search email threads and the user's long-term memory from past AI chats (projects, goals, "
                       "preferences, past attempts).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "thread_details",
        "description": "Full text of an email thread plus its graph context (people, commitments, linked projects).",
        "parameters": {"type": "object", "properties": {"thread_id": {"type": "string"}},
                       "required": ["thread_id"]}}},
    {"type": "function", "function": {
        "name": "draft_reply",
        "description": "Write a reply in the user's voice for that person and save it to Gmail Drafts. Never sends.",
        "parameters": {"type": "object", "properties": {
            "thread_id": {"type": "string"},
            "instructions": {"type": "string", "description": "what the reply should say"}},
            "required": ["thread_id"]}}},
    {"type": "function", "function": {
        "name": "run_cypher",
        "description": "Read-only Cypher against the user's graph, for questions the other tools don't cover. "
                       "Schema: " + Q.SCHEMA_HINT,
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]


def call(ctx: ToolContext, name: str, raw_args: str) -> tuple[str, Any, str]:
    """Execute a tool call. Returns (json for the LLM, raw result, cypher used)."""
    try:
        args = json.loads(raw_args or "{}")
        result, cypher = TOOLS[name](ctx, **args)
    except Exception as e:
        result, cypher = {"error": f"{type(e).__name__}: {e}"}, ""
    return json.dumps(result, default=str)[:RESULT_CHARS], result, cypher
