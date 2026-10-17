"""One LLM pass per conversation → Extraction, cached on disk by transcript hash.

The prompt includes the user's existing goals and active preferences from the graph, so the model can reuse them
(dedupe) or mark a new preference as replacing an old one (temporal memory).
"""

import hashlib
import json
from pathlib import Path

from groq import BadRequestError
from pydantic import ValidationError

from mnemo.extract.schema import DOMAINS, ENTITY_KINDS, Extraction
from mnemo.llm import GroqPool

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "extract"
TRANSCRIPT_BUDGET = 14000  # chars, about 3.5k tokens, to stay inside free-tier TPM
USER_MSG_CHARS = 1500
ASSISTANT_MSG_CHARS = 400

SYSTEM_PROMPT = f"""You extract long-term memory about the USER from one conversation they had with an AI assistant.
Return only a JSON object with this shape:
{{
  "domain": one of {list(DOMAINS)},
  "summary": "1-2 sentences: what the user was doing and how it ended",
  "topics": ["lowercase subject"],
  "entities": [{{"name": "lowercase name", "kind": one of {list(ENTITY_KINDS)}}}],
  "goals": [{{"text": "...", "existing_id": null}}],
  "tasks": [{{"text": "...", "outcome": "success|failed|abandoned|unknown", "outcome_summary": "...",
             "uses": ["entity name"], "goal": null}}],
  "preferences": [{{"text": "...", "scope": "context|domain|global", "existing_id": null, "replaces": null,
                   "correction": null}}]
}}
Rules:
- topics: 1-6 specific subjects ("jwt authentication", "graph databases"), not generic words like "code" or "help".
  Reuse an existing topic name exactly when it covers the same subject; invent a new one only when none fits.
- entities: up to 10 named things (tools, libraries, languages, services, people, organizations, places, products).
- goals: longer-term aims the user expressed, not single tasks. If one matches an existing goal, set existing_id.
- tasks: up to 5 concrete things the user tried to get done, with the outcome the transcript shows. "goal" is the
  existing goal id or the text of a goal from your "goals" list that the task serves, else null.
- preferences: only stable preferences about how the USER likes things done (tools, style, workflow, format),
  stated or clearly shown by the user, never by the assistant. If the user corrected the assistant, put their
  short words in "correction". Same as an existing preference → existing_id. Contradicts one → replaces = its id.
  scope: context = only this project, domain = this area of life, global = everywhere.
  NEVER list an existing preference just because it exists: only when the user says or shows it in THIS
  conversation. Most conversations have no preferences.
- Only include what the transcript supports. Empty lists are fine. Write everything in English."""


def build_transcript(conv: dict) -> str:
    header = [f"Title: {conv['title']}"]
    if conv.get("ctx_name"):
        header.append(f"Project: {conv['ctx_name']}")
    if conv.get("tool_calls"):
        files = ", ".join(Path(f).name for f in (conv.get("files") or [])[:10])
        header.append(f"Tool calls: {conv['tool_calls']} ({conv.get('tool_errors') or 0} failed). Files: {files}")

    lines = []
    for m in sorted(conv["messages"], key=lambda m: m["idx"]):
        limit = USER_MSG_CHARS if m["role"] == "user" else ASSISTANT_MSG_CHARS
        lines.append(f"{m['role'].upper()}: {m['text'][:limit]}")
    body = "\n\n".join(lines)
    if len(body) > TRANSCRIPT_BUDGET:
        # Keep the opening (intent) and the larger tail (how it ended).
        head = int(TRANSCRIPT_BUDGET * 0.4)
        body = body[:head] + "\n\n[… middle of conversation omitted …]\n\n" + body[-(TRANSCRIPT_BUDGET - head):]
    return "\n".join(header) + "\n---\n" + body


def _cache_path(conv_id: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha1(conv_id.encode()).hexdigest()[:16]}.json"


def load_cached(conv_id: str, transcript_hash: str) -> Extraction | None:
    path = _cache_path(conv_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Extraction.model_validate(data["extraction"]) if data.get("hash") == transcript_hash else None


def _save_cache(conv_id: str, transcript_hash: str, model: str, extraction: Extraction) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(conv_id).write_text(json.dumps({
        "conversation_id": conv_id, "hash": transcript_hash, "model": model,
        "extraction": extraction.model_dump(),
    }, indent=2))


def _memory_block(goals: list[dict], prefs: list[dict], topics: list[str]) -> str:
    goal_lines = "\n".join(f"- {g['id']}: {g['text']}" for g in goals) or "(none)"
    pref_lines = "\n".join(f"- {p['id']}: {p['text']}" for p in prefs) or "(none)"
    return (f"Existing topics: {', '.join(topics) or '(none)'}\n\n"
            f"Existing goals:\n{goal_lines}\n\nExisting active preferences:\n{pref_lines}")


def extract(llm: GroqPool, conv: dict, goals: list[dict], prefs: list[dict], topics: list[str],
            force: bool = False) -> tuple[Extraction, bool]:
    """Returns (extraction, from_cache)."""
    transcript = build_transcript(conv)
    transcript_hash = hashlib.sha1(transcript.encode()).hexdigest()
    if not force and (cached := load_cached(conv["id"], transcript_hash)):
        return cached, True

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{_memory_block(goals, prefs, topics)}\n\nConversation:\n{transcript}"},
    ]
    for attempt in range(3):
        try:
            resp = llm.chat(messages, model=llm.extract_model, response_format={"type": "json_object"},
                            temperature=0.2)
        except BadRequestError as e:
            # Groq rejects malformed JSON server-side (json_validate_failed); sampling again usually fixes it.
            if attempt == 2 or "json" not in str(e).lower():
                raise
            continue
        content = resp.choices[0].message.content or ""
        try:
            extraction = Extraction.model_validate_json(content)
            break
        except ValidationError as e:
            if attempt == 2:
                raise
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That JSON was invalid: {e.errors()[:3]}. Return corrected JSON only."},
            ]

    _save_cache(conv["id"], transcript_hash, resp.model, extraction)
    return extraction, False
