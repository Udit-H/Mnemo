"""Parse a ChatGPT export: conversations.json from Settings → Data controls → Export data.

Each conversation is a tree (`mapping`) because of edits and regenerations; we follow the branch the user
last saw, from `current_node` back to the root.
"""

import json
from pathlib import Path

from mnemo.ingest.models import Conversation, Message, append_message, to_datetime

TEXT_CONTENT_TYPES = ("text", "multimodal_text")


def _main_branch(conv: dict) -> list[dict]:
    mapping = conv.get("mapping") or {}
    chain, node_id = [], conv.get("current_node")
    while node_id and node_id in mapping:
        chain.append(mapping[node_id])
        node_id = mapping[node_id].get("parent")
    return list(reversed(chain))


def parse_conversation(conv: dict) -> Conversation | None:
    messages: list[Message] = []
    for node in _main_branch(conv):
        msg = node.get("message") or {}
        role = (msg.get("author") or {}).get("role")
        content = msg.get("content") or {}
        if role not in ("user", "assistant") or content.get("content_type") not in TEXT_CONTENT_TYPES:
            continue
        if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        text = "\n".join(p for p in content.get("parts") or [] if isinstance(p, str))
        append_message(messages, f"chatgpt:{msg.get('id') or node.get('id')}", role, text, msg.get("create_time"))

    if not any(m.role == "user" for m in messages):
        return None
    native_id = conv.get("conversation_id") or conv.get("id")
    return Conversation(
        id=f"chatgpt:{native_id}",
        source="chatgpt",
        title=conv.get("title") or messages[0].text[:80],
        started_at=to_datetime(conv.get("create_time")),
        ended_at=to_datetime(conv.get("update_time")),
        messages=messages,
    )


def parse_all(path: Path) -> list[Conversation]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [c for c in map(parse_conversation, data) if c is not None]
