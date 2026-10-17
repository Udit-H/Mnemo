# Architecture — Personal AI Agent with Graph Memory

> Neo4j × hackFront India Graph Builder Build Sprint (Bengaluru, 17 Oct 2026), **Open Problem Statement**.
> Working name: **Mnemo** (placeholder, rename freely).

## 1. Problem

AI assistants forget. Every new chat starts from zero: no memory of your goals, your preferences, what you tried
last week, or what worked. Existing "memory" features store flat facts ("user likes Python") with no structure, no
history, and no links between different parts of your life.

## 2. Solution

A personal AI agent whose memory is a **Neo4j knowledge graph** spanning every domain of the user's life (coding,
learning, planning, health, finance, career and others). The agent:

1. **Recalls** relevant context by *traversing* the graph from what you just said.
2. **Answers** using that connected context.
3. **Remembers** new facts, preferences, tasks, outcomes and corrections by writing them back to the graph.
4. **Explains** which subgraph it used, so the reasoning is visible.

### Why a graph (the pitch to judges)

| Capability | Flat memory / vector store | Graph memory |
|---|---|---|
| "What did I try before and did it work?" | Similar text, no outcome link | `Task -[:LED_TO]-> Outcome` |
| Preferences that apply only in a context | Global facts, conflicts | `Preference -[:APPLIES_IN]-> Domain/Context` |
| Preferences that change over time | Overwritten or duplicated | `since`/`until` on edges (temporal memory) |
| Cross-domain insight | Not possible | Multi-hop paths across domains |
| Explainability | "Top-k chunks" | The actual path that was followed |

**The moment to show judges:** a single question whose answer comes from a path crossing 3 domains, shown visually.

## 3. System overview

```
 ┌───────────────────────── Ingestion (batch) ─────────────────────────┐
 │  ChatGPT export ─┐                                                   │
 │  Claude.ai export ├─► Parsers ─► LLM Extractor ─► Graph Writer ──┐   │
 │  Claude Code logs ┘   (normalize to     (structured output,     │   │
 │  (stretch: Calendar,   Conversation/     fixed schema)           │   │
 │   Notion)              Message)                                  │   │
 └──────────────────────────────────────────────────────────────────┼───┘
                                                                    ▼
                                                        ┌──────────────────┐
                                                        │      Neo4j       │
                                                        │ graph + vector + │
                                                        │ fulltext indexes │
                                                        └──────────────────┘
                                                           ▲          │
 ┌──────────────────────── Agent loop (live) ──────────────┼──────────┼─┐
 │  User msg ─► Recall (entity link + traversal + vector) ─┘          │ │
 │          ─► Answer (LLM + context subgraph)                        │ │
 │          ─► Remember (extract ─► Graph Writer) ────────────────────┘ │
 │          ─► Explain (return subgraph for UI)                         │
 └──────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                    Streamlit UI: chat panel + graph panel + insights panel
```

## 4. Graph model

All domains share one schema. "Domain" is data, not code, so a new domain needs no new pipeline.

### Nodes

| Label | Key props | Notes |
|---|---|---|
| `Me` | `id` | Single node, the user |
| `Domain` | `name` | coding, learning, planning, health, finance, career, … |
| `Context` | `id`, `name`, `kind` | Project, course, trip, job search: an ongoing area of activity |
| `Conversation` | `id`, `source`, `title`, `started_at` | One chat/session from any source |
| `Message` | `id`, `role`, `text`, `ts`, `embedding` | Optional; store for recall and vector search |
| `Topic` | `name`, `embedding` | Normalized, lowercase |
| `Entity` | `name`, `kind` | Person, place, tool, product, organization, … |
| `Goal` | `id`, `text`, `status` | |
| `Task` | `id`, `text`, `status`, `ts` | Something the user tried or plans to do |
| `Outcome` | `id`, `kind`, `summary` | `kind`: success / failed / abandoned / corrected |
| `Preference` | `id`, `text` | "Use pnpm, not npm", "Keep emails short" |
| `Correction` | `id`, `text`, `ts` | User correcting the assistant; the source of preferences |

### Relationships

```
(:Me)-[:HAS_GOAL]->(:Goal)-[:IN_DOMAIN]->(:Domain)
(:Me)-[:PREFERS {since, until, confidence}]->(:Preference)-[:APPLIES_IN]->(:Domain|:Context)
(:Me)-[:WORKS_ON]->(:Context)-[:IN_DOMAIN]->(:Domain)
(:Me)-[:KNOWS]->(:Entity {kind:'person'})
(:Conversation)-[:IN_CONTEXT]->(:Context)
(:Conversation)-[:HAS_MESSAGE]->(:Message)
(:Conversation)-[:ABOUT]->(:Topic)-[:RELATED_TO]->(:Topic)
(:Conversation)-[:MENTIONS]->(:Entity)
(:Conversation)-[:FOLLOWED_BY]->(:Conversation)
(:Task)-[:FROM]->(:Conversation)
(:Task)-[:SERVES]->(:Goal)
(:Task)-[:LED_TO]->(:Outcome)
(:Task)-[:USES]->(:Entity)
(:Correction)-[:IN]->(:Conversation)
(:Correction)-[:UPDATED]->(:Preference)
(:Topic)-[:IN_DOMAIN]->(:Domain)
```

**Temporal memory:** when a preference changes, set `until` on the old `PREFERS` edge and create a new one. Active
preferences are `WHERE r.until IS NULL`.

### Constraints and indexes

- Uniqueness on `id` (or `name` for `Domain`, `Topic`, `Entity`); writes use `MERGE`.
- Vector index on `Topic.embedding` and `Message.embedding`.
- Fulltext index over `Entity.name`, `Topic.name`, `Context.name`, `Goal.text` for entity linking.

## 5. Components

### 5.1 Ingestion

| Source | Location | Parser output |
|---|---|---|
| ChatGPT export | `conversations.json` from Settings → Data controls → Export | Conversation + Messages |
| Claude.ai export | `conversations.json` from Settings → Privacy → Export | Conversation + Messages |
| Claude Code logs | `~/.claude/projects/<project>/*.jsonl` | Conversation (session) + Messages + tool calls + files touched |
| Stretch: Calendar / Notion | API / MCP | Context, Task, Entity |

All parsers emit the same normalized `Conversation{source, id, title, started_at, messages[]}` shape, so extraction and
graph writing are shared.

### 5.2 Extractor

- One LLM call per conversation (chunk long ones), using **structured output** against a fixed JSON schema that matches
  the node and relationship types above.
- Output: `{domain, context, topics[], entities[], goals[], tasks[{text, outcome}], preferences[], corrections[]}`.
- Normalize names (lowercase, trim) before `MERGE` to avoid duplicates. Fuzzy-merge topics using embedding similarity.
- **Time-box quality work.** Good enough beats perfect.

### 5.3 Graph Writer

Idempotent `MERGE`-based Cypher, one transaction per conversation. Re-running ingestion must not create duplicates.

### 5.4 Recall (retrieval)

1. **Seed:** fulltext and vector match of the user message against `Entity`, `Topic`, `Context`, `Goal`.
2. **Expand:** 1–2 hop traversal from the seeds to collect active `Preference`s, related `Goal`s, past `Task → Outcome`
   pairs and recent related `Conversation`s.
3. **Rank:** recency, hop distance and match score. Cap the context size.
4. Return both a **text context** (for the LLM) and the **subgraph** (for the UI).

```cypher
// Illustrative
CALL db.index.fulltext.queryNodes('memory_names', $q) YIELD node AS seed, score
MATCH p = (seed)-[*1..2]-(m)
WHERE m:Preference OR m:Goal OR m:Outcome OR m:Task OR m:Conversation
RETURN p, score ORDER BY score DESC LIMIT 50
```

### 5.5 Agent

- LLM: Groq via `mnemo/llm.py` (`GroqPool`: key rotation + model fallback on rate limits). Agent model
  `GROQ_AGENT_MODEL`, extraction model `GROQ_EXTRACT_MODEL` (needs JSON / structured output support).
- Prompt = system persona + recalled context + chat history.
- Tools available to the agent (optional): `recall(query)`, `remember(fact)`, `cypher_readonly(query)`.
- After each turn, run the extractor on the new exchange and write the results to the graph (live memory).

### 5.6 UI (Streamlit)

- **Chat panel:** talk to the agent.
- **Graph panel:** the subgraph used for the last answer (pyvis / streamlit-agraph).
- **Insights panel:** canned Cypher queries, such as top recurring topics, goals with no recent activity, approaches
  that failed repeatedly, preferences by domain, and cross-domain bridges.

## 6. Tech stack

- Python 3.11+, `neo4j` driver, `groq` SDK, `python-dotenv`, `streamlit`, `pydantic` (extraction schemas)
- LLM: Groq free tier, with multiple keys rotated by `mnemo/llm.py`
- Neo4j AuraDB Free (or local Neo4j Desktop / Docker)
- Embeddings: local `sentence-transformers/all-MiniLM-L6-v2` (384-dim) → Neo4j vector index

Environment variables: see `.env.example`. Copy it to `.env`, which is gitignored.

## 7. Planned repo layout

```
mnemo/
  graph/      db.py (driver), schema.cypher (constraints/indexes), writer.py, queries.py
  ingest/     chatgpt.py, claude_ai.py, claude_code.py  → normalized Conversation
  extract/    schema.py (pydantic), extractor.py
  agent/      recall.py, agent.py
app/          streamlit_app.py
scripts/      ingest.py (CLI), seed_demo.py
data/         raw exports (gitignored, personal data)
docs/         ARCHITECTURE.md
```

## 8. Demo script (≈3 min)

1. Show the graph in Neo4j Bloom: hundreds of real conversations across domains, linked.
2. **Coding:** "Help me set up auth for my app." The agent recalls your stack preferences and a past auth bug.
3. **Learning:** "What should I study next?" It links your *AI engineer* goal to the skills you actually used.
4. **Planning:** "Plan my weekend." It knows the hackathon deadline and your preferences.
5. **Correction → memory:** "No, I use pnpm." Show the new `Preference` node and the old edge getting `until`.
6. **Cross-domain moment:** one answer drawing on all three domains. Show the path in the graph panel.
7. Insights panel: patterns across your life, all computed with Cypher.

## 9. Scope and risks

| Risk | Mitigation |
|---|---|
| Extraction eats the day | Fixed schema, one pass, cap conversations ingested (e.g. most recent 200) |
| Duplicate / messy nodes | Normalize + MERGE; embedding-based topic merge only if time allows |
| Personal data leak | `data/` and `.env` gitignored; use a curated subset for the demo |
| Demo depends on live LLM | Pre-ingest; keep a seeded demo graph as backup (`scripts/seed_demo.py`) |

## 10. Milestones (hack day)

1. **M0 – Setup:** repo scaffold, Aura instance, `.env`, schema constraints.
2. **M1 – Ingest:** parsers for one export + Claude Code logs → normalized conversations.
3. **M2 – Extract + write:** structured extraction → graph. Inspect in Bloom.
4. **M3 – Recall + agent:** retrieval queries + chat loop with live memory write-back.
5. **M4 – UI:** Streamlit chat + graph panel + insights.
6. **M5 – Demo polish:** seed data, rehearse the script, backup graph.
