# Mnemo: a personal assistant that adapts to you through a graph

> Neo4j × hackFront India Graph Builder Build Sprint, Bengaluru, 17 Oct 2026 (Open Problem Statement)

Mnemo is a PA that works on your Gmail. It triages your inbox, tracks who owes what, and drafts replies in your
voice. **What makes it different is how it learns:** a Neo4j graph models who matters to you (from how you
actually reply), what you've promised people, how you write to each person, and what you've been working on (from
your past AI chats). Every click and every draft edit changes that graph, so the PA improves the more you use it.

The graph does the work: inbox ranking, "you owe" reminders, per-person writing style and cross-domain links are all
**Cypher over relationships**, and the UI shows that Cypher. The LLM only reads and writes text around it.

## What it does

| | How the graph does it |
|---|---|
| **Inbox ranked by what matters to *you*** | `(Me)-[:CORRESPONDS_WITH {reply_rate, avg_reply_hours, sent, received, importance}]->(Person)` is computed from real email behaviour (no LLM), then combined with needs-reply, urgency, commitments and project links. Each ranking comes with its reasons. |
| **Who owes what** | `(Thread)-[:HAS_COMMITMENT]->(Commitment {due, status})-[:OWED_BY]->(Person or Me)` |
| **Replies in your voice, per person** | `(Preference)-[:APPLIES_TO]->(Person or Org)`, learned from **your edits** to its drafts |
| **Email ↔ your projects** | `(Thread)-[:IN_CONTEXT]->(Context)<-[:IN_CONTEXT]-(Conversation)`: an email about the Neo4j sprint links to your `Neo4J-Hack` project and the Claude Code session where you planned it |
| **Adapts over time** | Every action is `(Me)-[:GAVE]->(Feedback)-[:ON]->(…)` and changes importance, dismissals, commitments or style |
| **An agent that queries the graph** | Tool-calling LLM: `triage_inbox`, `person_context`, `open_commitments`, `search_memory`, `thread_details`, `draft_reply`, read-only `run_cypher`. Each step's Cypher is shown. |
| **Safe by design** | Gmail scopes are read + compose. Drafts go to Gmail Drafts. **It never sends email.** |

## Architecture

```
 Gmail API ──► headers ──────────────► Person / Org / Thread / Email + CORRESPONDS_WITH stats  (Cypher, no LLM)
           └─► full text (top threads) ► LLM extraction ─► Commitments, Topics, links to Contexts/Goals
 AI chat logs (Claude Code, Gemini) ──► LLM extraction ─► Conversations, Goals, Tasks→Outcomes, Preferences, Topics
                                                     │
                                                     ▼
                                              ┌─────────────┐
   Streamlit PA UI  ◄──── tool-calling agent ─┤    Neo4j    │◄── feedback: dismiss / ⬆ / ✓ / draft edits
   (inbox, thread graph, drafts, Ask, learned) └─────────────┘
```

- **Neo4j AuraDB**: graph, fulltext indexes (entity linking, thread search) and vector indexes (topic and message
  embeddings).
- **Groq** (`openai/gpt-oss-120b`, with key rotation and model fallback): extraction, the agent and drafting.
- **Local embeddings** (`all-MiniLM-L6-v2`): semantic recall over chat memory.

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Section 0 covers the Gmail PA; sections 1–10 cover the chat
memory layer.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Neo4j Aura creds, Groq keys (comma-separated), SELF_ALIASES
```

Gmail: create a Google Cloud project → enable the **Gmail API** → OAuth consent screen (External, add yourself as a
test user) → create a **Desktop app** OAuth client → save the JSON as `secrets/gmail_credentials.json`. Then:

```bash
python -m scripts.gmail_auth         # one-time browser login → secrets/gmail_token.json
python -m scripts.init_db            # preflight: Neo4j schema, Groq keys, Gmail; prints ✓/✗
```

## Load data

```bash
python -m scripts.gmail_sync                   # last 90 days of email → people, orgs, threads, relationship stats
python -m scripts.email_extract --limit 40     # read top threads: commitments, needs-reply, project links
python -m scripts.ingest claude_code           # optional: AI chat memory (also gemini / chatgpt / claude_ai)
python -m scripts.extract                      # LLM extraction for chat memory (cached)
python -m scripts.embed                        # local embeddings for semantic recall
python -m scripts.refresh                      # all of the above for new data, in one go
```

## Run

```bash
streamlit run app/streamlit_app.py             # the PA UI
python -m scripts.pa --trace                   # the same agent in the terminal, printing its Cypher
```

## Demo script (≈3 min)

1. **Inbox:** "Ranked by how I behave. I answer Shriniwas 100% of the time in about 4.5h, so he ranks high. Devpost
   sends a lot and I never reply, so it ranks low." Point at the reasons under each thread.
2. **Open the Neo4j Build Sprint thread:** the graph shows email → `Neo4J-Hack` project → the Claude Code chat where
   I planned this hack. Email and AI-chat memory are one graph.
3. **Draft a reply, edit it** (shorter, my sign-off), and **save.** It says "🧠 Learned: … (person)". Draft again and
   the new draft is already in that style. Check Gmail Drafts: there's a draft and nothing was sent.
4. **✕ Dismiss** a noisy thread. The toast shows the sender's importance dropping, and the inbox re-ranks.
5. **Ask Mnemo:** "What do I owe people this week?" Expand the tool step to show the Cypher and its rows.
6. **What Mnemo learned:** the feedback log, per-person styles, and importance including "from your feedback".

After rehearsing, use **Reset demo feedback** in the sidebar.

## Useful Cypher (Aura Query tab / Bloom)

```cypher
// Who matters, from real behaviour
MATCH (:Me)-[c:CORRESPONDS_WITH]->(p:Person) RETURN p.name, c.reply_rate, c.avg_reply_hours, c.importance
ORDER BY c.importance DESC LIMIT 10;

// Email linked to projects and the AI chats about them
MATCH p=(:Thread)-[:IN_CONTEXT]->(:Context)<-[:IN_CONTEXT]-(:Conversation) RETURN p;

// What I owe, and to whom
MATCH (t:Thread)-[:HAS_COMMITMENT]->(c:Commitment {owner:'me', status:'open'}) RETURN t.subject, c.text, c.due;

// How Mnemo has adapted
MATCH p=(:Me)-[:GAVE]->(:Feedback)-[:TAUGHT|ON|ABOUT_PERSON]->() RETURN p;
```

## Fallback if Aura or the wifi fails

```bash
python -m scripts.backup export                                         # → data/backup/graph-*.json
docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:5
python -m scripts.backup restore data/backup/graph-….json --uri bolt://localhost:7687 --password password123
# then point NEO4J_URI/NEO4J_PASSWORD in .env at bolt://localhost:7687
```

## Repo map

```
mnemo/gmail/      Gmail OAuth, thread fetch, drafts (no send)
mnemo/graph/      all Cypher: schema, email graph + stats, PA tool queries, feedback, insights, chat memory
mnemo/extract/    LLM extraction (chat memory, email threads), cached in data/cache/
mnemo/agent/      PA tool-calling agent + tools, learning from draft edits, chat-memory recall
app/              Streamlit UI + pyvis graph rendering
scripts/          auth, sync, extract, ingest, embed, refresh, backup, preflight, CLI chats
```

`secrets/`, `.env` and `data/` (personal email, chats, caches, backups) are gitignored.
