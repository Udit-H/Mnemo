"""Parse Claude Code session logs: ~/.claude/projects/<project>/<session>.jsonl.

Subagent logs (<session>/subagents/*.jsonl) are skipped; their work shows up as tool calls in the main session.
"""

import json
import re
from pathlib import Path

from mnemo.ingest.models import ContextRef, Conversation, Message, ToolCall, append_message, to_datetime

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

_REMINDER = re.compile(r"<(system-reminder|task-notification)>.*?</\1>", re.DOTALL)
_NOISE_PREFIXES = ("<command-", "<local-command", "Caveat: The messages below")


def _clean(text: str) -> str:
    text = _REMINDER.sub("", text).strip()
    return "" if text.startswith(_NOISE_PREFIXES) else text


def _target(tool_input: dict) -> str | None:
    for key in ("file_path", "notebook_path", "path", "url", "pattern"):
        if tool_input.get(key):
            return str(tool_input[key])
    if tool_input.get("command"):
        return str(tool_input["command"])[:120]
    return None


def parse_session(path: Path) -> Conversation | None:
    title = None
    cwd = None
    messages: list[Message] = []
    tools: dict[str, ToolCall] = {}
    timestamps = []

    for line in path.open(encoding="utf-8"):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if kind == "ai-title":
            title = entry.get("aiTitle") or title
            continue
        if kind not in ("user", "assistant") or entry.get("isSidechain") or entry.get("isMeta"):
            continue

        cwd = cwd or entry.get("cwd")
        if entry.get("timestamp"):
            timestamps.append(entry["timestamp"])
        content = (entry.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []

        texts = []
        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                texts.append(_clean(block.get("text", "")))
            elif btype == "tool_use":
                tools[block["id"]] = ToolCall(name=block.get("name", "?"), target=_target(block.get("input") or {}))
            elif btype == "tool_result" and block.get("is_error") and block.get("tool_use_id") in tools:
                tools[block["tool_use_id"]].is_error = True

        append_message(messages, f"claude_code:{entry['uuid']}", kind, "\n".join(t for t in texts if t),
                       entry.get("timestamp"))

    if not any(m.role == "user" for m in messages):
        return None

    first_prompt = next(m.text for m in messages if m.role == "user")
    return Conversation(
        id=f"claude_code:{path.stem}",
        source="claude_code",
        title=title or first_prompt.splitlines()[0][:80],
        started_at=to_datetime(min(timestamps)) if timestamps else None,
        ended_at=to_datetime(max(timestamps)) if timestamps else None,
        messages=messages,
        tool_calls=list(tools.values()),
        context=ContextRef(id=f"project:{cwd}", name=Path(cwd).name, kind="project", domain="coding") if cwd else None,
    )


def parse_all(root: Path = DEFAULT_ROOT) -> list[Conversation]:
    sessions = (parse_session(p) for p in sorted(root.glob("*/*.jsonl")))
    return [c for c in sessions if c is not None]
