"""One LLM pass per email thread → ThreadExtraction, cached by content hash.

Graph-informed like the chat extractor: the prompt lists the user's existing projects (Contexts), goals and
topics so threads link into v1 memory instead of creating parallel islands.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from groq import BadRequestError
from pydantic import BaseModel, ValidationError, field_validator

from mnemo.extract.schema import _pick, norm
from mnemo.llm import GroqPool

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "email"
MSG_CHARS = 1800
MAX_MESSAGES = 6
CATEGORIES = ("action_needed", "waiting_on_them", "scheduling", "opportunity", "fyi", "personal", "automated")
URGENCY = ("high", "normal", "low")


class Commitment(BaseModel):
    text: str
    owner: str = "me"  # me = the user owes it; them = the other side owes it
    due: str | None = None  # YYYY-MM-DD if a date is stated or clearly implied
    status: str = "open"

    @field_validator("owner")
    @classmethod
    def _owner(cls, v: str) -> str:
        return _pick(v, ("me", "them"), "me")

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        return _pick(v, ("open", "done"), "open")

    @field_validator("due")
    @classmethod
    def _due(cls, v: str | None) -> str | None:
        try:
            return date.fromisoformat(v).isoformat() if v else None
        except ValueError:
            return None


class ThreadExtraction(BaseModel):
    summary: str = ""
    category: str = "fyi"
    needs_reply: bool = False
    urgency: str = "normal"
    commitments: list[Commitment] = []
    topics: list[str] = []
    context: str | None = None  # name of an existing Context (project)
    goal_id: str | None = None  # id of an existing Goal

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        return _pick(v, CATEGORIES, "fyi")

    @field_validator("urgency")
    @classmethod
    def _urgency(cls, v: str) -> str:
        return _pick(v, URGENCY, "normal")

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(norm(t) for t in v if t and t.strip()))[:5]


SYSTEM_PROMPT = f"""You are the user's personal assistant reading one of their email threads.
Return only a JSON object:
{{
  "summary": "1-2 sentences: what this thread is about and where it stands now",
  "category": one of {list(CATEGORIES)},
  "needs_reply": true if the latest message expects a reply or action from the user,
  "urgency": "high|normal|low",
  "commitments": [{{"text": "...", "owner": "me|them", "due": "YYYY-MM-DD or null", "status": "open|done"}}],
  "topics": ["lowercase subject"],
  "context": "name of one existing project from the list, or null",
  "goal_id": "id of one existing goal from the list, or null"
}}
Rules:
- commitments: concrete promises or requests. owner "me" = the user promised or was asked to do it; "them" = the
  other person owes it. Resolve relative dates ("Friday", "tomorrow") against today's date. Mark done only if the
  thread shows it was done.
- topics: 1-5; reuse an existing topic name exactly when it fits.
- context/goal_id: only if the thread is clearly about that project or goal; otherwise null.
- automated = receipts, notifications, OTPs, marketing. Those have no commitments and needs_reply=false.
Only include what the thread supports."""


def thread_transcript(messages: list[dict], self_emails: list[str]) -> str:
    lines = []
    for m in messages[-MAX_MESSAGES:]:
        who = "USER (me)" if m["from"].email in self_emails else (m["from"].name or m["from"].email)
        lines.append(f"[{m['at']:%Y-%m-%d %H:%M}] FROM {who} <{m['from'].email}>\n{m['text'][:MSG_CHARS]}")
    return f"Subject: {messages[0]['subject']}\n\n" + "\n\n---\n\n".join(lines)


def _cache_path(thread_id: str) -> Path:
    return CACHE_DIR / f"{thread_id}.json"


def extract_thread(llm: GroqPool, thread_id: str, transcript: str, contexts: list[str], goals: list[dict],
                   topics: list[str], force: bool = False) -> tuple[ThreadExtraction, bool]:
    """Returns (extraction, from_cache)."""
    content_hash = hashlib.sha1(transcript.encode()).hexdigest()
    path = _cache_path(thread_id)
    if not force and path.exists():
        cached = json.loads(path.read_text())
        if cached.get("hash") == content_hash:
            return ThreadExtraction.model_validate(cached["extraction"]), True

    memory = (f"Today: {date.today().isoformat()}\n"
              f"Existing projects: {', '.join(contexts) or '(none)'}\n"
              f"Existing goals:\n" + ("\n".join(f"- {g['id']}: {g['text']}" for g in goals) or "(none)") +
              f"\nExisting topics: {', '.join(topics) or '(none)'}")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{memory}\n\nThread:\n{transcript}"}]
    for attempt in range(3):
        try:
            resp = llm.chat(messages, model=llm.extract_model, response_format={"type": "json_object"},
                            temperature=0.2)
        except BadRequestError as e:
            if attempt == 2 or "json" not in str(e).lower():
                raise
            continue
        try:
            extraction = ThreadExtraction.model_validate_json(resp.choices[0].message.content or "")
            break
        except ValidationError:
            if attempt == 2:
                raise

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"thread_id": thread_id, "hash": content_hash,
                                "extraction": extraction.model_dump()}, indent=2))
    return extraction, False
