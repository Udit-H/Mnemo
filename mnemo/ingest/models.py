"""Normalized shapes every parser emits. Extraction and the graph writer only see these."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

MAX_MESSAGE_CHARS = 4000


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    ts: datetime | None = None


class ToolCall(BaseModel):
    name: str
    target: str | None = None  # file path, command or URL the tool acted on
    is_error: bool = False


class ContextRef(BaseModel):
    """An ongoing area of activity (project, course, trip). Ids are source-independent, e.g. project:<path>."""

    id: str
    name: str
    kind: str
    domain: str | None = None


class Conversation(BaseModel):
    id: str  # "<source>:<native id>"
    source: Literal["claude_code", "chatgpt", "claude_ai", "gemini"]
    title: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    messages: list[Message]
    tool_calls: list[ToolCall] = []
    context: ContextRef | None = None


def to_datetime(value: str | float | int | None) -> datetime | None:
    """Parse an ISO string or a Unix epoch into an aware datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def append_message(messages: list[Message], msg_id: str, role: str, text: str, ts) -> None:
    """Append, merging consecutive same-role messages so one turn becomes one Message."""
    text = text.strip()
    if not text:
        return
    if messages and messages[-1].role == role:
        prev = messages[-1]
        prev.text = (prev.text + "\n\n" + text)[:MAX_MESSAGE_CHARS]
        return
    messages.append(Message(id=msg_id, role=role, text=text[:MAX_MESSAGE_CHARS], ts=to_datetime(ts)))
