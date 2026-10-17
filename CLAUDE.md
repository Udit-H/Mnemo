# CLAUDE.md

## Project

Hackathon project for the **Neo4j × hackFront India Graph Builder Build Sprint** (Bengaluru, Sat 17 Oct 2026),
**Open Problem Statement**. One-day build. Judged on problem solving, technical implementation, **effective use of
Neo4j**, innovation and practicality. Judges want to see the graph doing the work, not just a UI with a database
attached.

**Idea (finalized):** a personal AI agent whose long-term memory is a Neo4j knowledge graph that spans all domains of
the user's life (coding, learning, planning, …). It recalls context by graph traversal, answers with it, writes new
memories (facts, preferences, tasks, outcomes, corrections) back to the graph, and shows the subgraph it used.
Working name: **Mnemo**.

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
- [ ] M4 Streamlit UI (chat + graph + insights)
- [ ] M5 Demo polish + seeded backup graph

Update this checklist as milestones land.
