"""Parse a Claude.ai export: conversations.json from Settings → Privacy → Export data."""

import json
from pathlib import Path

from mnemo.ingest.models import Conversation, Message, append_message, to_datetime

ROLES = {"human": "user", "assistant": "assistant"}


def _text(msg: dict) -> str:
    blocks = [b.get("text", "") for b in msg.get("content") or [] if b.get("type") == "text"]
    return "\n".join(blocks) or msg.get("text") or ""


def parse_conversation(conv: dict) -> Conversation | None:
    messages: list[Message] = []
    for msg in conv.get("chat_messages") or []:
        role = ROLES.get(msg.get("sender"))
        if role:
            append_message(messages, f"claude_ai:{msg.get('uuid')}", role, _text(msg), msg.get("created_at"))

    if not any(m.role == "user" for m in messages):
        return None
    return Conversation(
        id=f"claude_ai:{conv['uuid']}",
        source="claude_ai",
        title=conv.get("name") or messages[0].text[:80],
        started_at=to_datetime(conv.get("created_at")),
        ended_at=to_datetime(conv.get("updated_at")),
        messages=messages,
    )


def parse_all(path: Path) -> list[Conversation]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [c for c in map(parse_conversation, data) if c is not None]
