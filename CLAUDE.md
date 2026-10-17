# CLAUDE.md

## Project

Hackathon project for the **Neo4j × hackFront India Graph Builder Build Sprint** (Bengaluru, Sat 17 Oct 2026),
**Open Problem Statement**. One-day build. Judged on problem solving, technical implementation, **effective use of
Neo4j**, innovation and practicality. Judges want to see the graph doing the work, not just a UI with a database
attached.

**Idea (v2, current): Mnemo is a personal assistant (PA) agent on the user's Gmail that adapts to them through a
Neo4j graph.** It triages, summarizes, tracks commitments and drafts replies. Every email, contact and user action
(approve / edit / dismiss) feeds the graph, so ranking and drafting change over time: who matters
(`CORRESPONDS_WITH` stats from real behaviour), what the user owes people (commitments), and how they write to each
person (preferences scoped to a Person/Org). **Drafts only. The agent never sends email.** Gmail comes first; other
tools come later.

v1 (built, now the foundation): long-term memory from AI chat logs (Claude Code + Gemini) → Goals, Contexts,
Topics, Preferences. In v2 this is context about what the user is working on, linked to email.

**Read `docs/ARCHITECTURE.md` before making design changes.** It is the source of truth for the graph schema,
components, repo layout, demo script and milestones. If you change the design, update that doc in the same change.

## Key decisions

- Single domain-agnostic schema. `Domain` is a node, so adding a domain must not need a new pipeline.
- Data sources: ChatGPT / Claude.ai exports, Claude Code logs (`~/.claude/projects/**/*.jsonl`) and live chat.
  Calendar / Notion are stretch goals only.
- All parsers normalize to one `Conversation` shape, followed by shared extraction and a shared graph writer.
- Temporal preferences: `PREFERS {since, until}`. Never delete or overwrite a preference; close the old edge instead.
- LLM: **Groq** (free tier). Always call it through `mnemo/llm.py` (`GroqPool`), never `Groq()` directly. It
  rotates `GROQ_API_KEYS` and falls back across models on 429. Model IDs come from `.env` (`GROQ_AGENT_MODEL`,
  `GROQ_EXTRACT_MODEL`, `GROQ_FALLBACK_MODELS`).
- Embeddings: local `sentence-transformers` (Groq has no embeddings API); dimension from `EMBEDDING_DIM`.
- Stack: Python 3.11+, `neo4j` driver, `groq`, `python-dotenv`, `pydantic`, `sentence-transformers`, `streamlit`;
  Neo4j AuraDB Free.
- Free-tier budget: rate limits are tight, so cap the number of conversations ingested, cache extraction results
  to `data/cache/`, and don't re-extract a conversation that is already cached.

## Conventions

- Graph writes are idempotent: `MERGE` on unique keys, one transaction per conversation. Re-running ingestion must
  not create duplicates.
- Normalize names (lowercase, trimmed) before merging `Topic` / `Entity`.
- Keep Cypher in `mnemo/graph/` (`queries.py`, `schema.cypher`), not scattered through app code.
- Secrets live in `.env` (template: `.env.example`). Never commit `.env`.
- `data/` holds personal chat exports and is gitignored. Never commit raw personal data or print large chunks of it.
- Optimize for a working demo over completeness: time-box extraction quality and prefer a simple, visible feature
  over a clever, hidden one.

## Status

- [x] Idea + architecture (`docs/ARCHITECTURE.md`)
- [x] M0 Setup: AuraDB connected, schema applied, Groq keys checked. Re-verify any time with
      `./venv/bin/python -m scripts.init_db`. The venv is at `./venv`.
- [x] M1 Ingest: `python -m scripts.ingest {claude_code|chatgpt|claude_ai|gemini} [path] [--limit N] [--dry-run]`.
      Loaded: Claude Code (25 sessions, 11 project Contexts) and Gemini (469 conversations from
      `data/gemini/Takeout/My Activity/Gemini Apps/MyActivity.html`). The ChatGPT and Claude.ai parsers are tested
      only on samples shaped like the export format.
- [x] M2 Extract: `python -m scripts.extract [--source S] [--limit N] [--force] [--reset]`. `--limit N` = the N
      most recent. Extracted: all 25 Claude Code conversations and the 40 most recent Gemini ones (209 topics,
      242 entities, 12 goals, 146 tasks, 13 preferences across coding/learning/career/planning/personal). Groq free
      tier: extract Gemini in batches of about 40, not all 469 at once. Known weakness: topic reuse is low.
      Embedding-based topic merge is planned for M3. Cross-source bridges exist (jwt, fastapi, duckdb, bm25 link
      Gemini ↔ Claude Code across coding/learning/career).
- [x] M3 Recall + agent: `mnemo/agent/recall.py` (seed → expand → gather; returns LLM context + subgraph) and
      `mnemo/agent/agent.py` (recall → answer → remember: each chat is a `live` Conversation, re-extracted every
      turn). Terminal chat: `python -m scripts.chat [--show-memory] [--no-remember]`. `--forget` deletes all live
      sessions (run it after rehearsals so test chats don't pollute memory). After ingest/extract, run
      `python -m scripts.embed` to embed new Topics and user Messages (local model).
- [x] M4 UI: `./venv/bin/streamlit run app/streamlit_app.py`. Chat tab: chat + per-turn "memory used" pyvis
      graph (`app/graph_view.py`) + metrics. Insights tab: canned Cypher from `mnemo/graph/insights.py`, each
      shown with its query. Sidebar: graph stats, remember toggle, new session, "Forget all live sessions".
      Headless test: `streamlit.testing.v1.AppTest` (see the M4 commit).
- [ ] M5 Demo polish + seeded backup graph (after v2)

### v2: Gmail PA
- [x] G1 Gmail connector (`mnemo/gmail/`): OAuth (`secrets/gmail_credentials.json` → `secrets/gmail_token.json`,
      scopes readonly + compose), fetch threads, create drafts. Login: `python -m scripts.gmail_auth`.
- [x] G2 Email → graph (no LLM): Person, Org, Thread, Email + `CORRESPONDS_WITH` stats/importance.
      `python -m scripts.gmail_sync [--days N --max N | --stats-only]`. Importance = reply behaviour + volume +
      Gmail Important/Personal share + recency + feedback. `SELF_ALIASES` in .env = the user's other addresses.
- [x] G3 `python -m scripts.email_extract [--limit 40 --days 60]`: Thread summary/category/needs_reply/urgency,
      `(:Thread)-[:HAS_COMMITMENT]->(:Commitment {owner, due, status})-[:OWED_BY]->(Person|Me)`, ABOUT Topic,
      IN_CONTEXT Context / RELATES_TO Goal (links email to v1 memory). Cache: data/cache/email/.
- [x] G4 `mnemo/agent/pa_agent.py` + `mnemo/agent/tools.py` (Cypher in `mnemo/graph/pa.py`): Groq tool-calling
      loop over triage_inbox, person_context, open_commitments, search_memory, thread_details, draft_reply
      (Gmail Drafts only), run_cypher (read-only session). Each step is traced with its Cypher. CLI:
      `python -m scripts.pa [--trace]`.
- [ ] G5 Feedback loop: approve/edit/dismiss → graph updates (scoped preferences, importance boosts).
- [ ] G6 PA UI: graph-ranked inbox with reasons, thread context panel, draft, agent chat.

Update this checklist as milestones land.
