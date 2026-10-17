"""Parse a Gemini Google Takeout export: Takeout/My Activity/Gemini Apps/MyActivity.html.

Each activity card holds "Prompted <prompt>", a timestamp, then the response as HTML. Takeout does not record
conversation boundaries, so prompts are grouped into conversations by time: a gap over SESSION_GAP starts a new one.
Only "Prompted" cards are kept (Live voice sessions, uploads, canvas edits and scheduled actions are skipped).
"""

import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.ingest.models import Conversation, Message, append_message

SESSION_GAP = timedelta(minutes=30)
TZ_OFFSETS = {"IST": timedelta(hours=5, minutes=30), "UTC": timedelta(0), "GMT": timedelta(0)}

_CARD_BODY = re.compile(
    r'mdl-typography--body-1">(.*?)</div><div class="content-cell mdl-cell mdl-cell--6-col '
    r'mdl-typography--body-1 mdl-typography--text-right', re.S)
_TIMESTAMP = re.compile(r"^([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2})\s*([AP]M)\s+([A-Z]{2,5})$")
_BLOCK_TAGS = re.compile(r"<(?:br|/p|/li|/h\d|hr|/tr)[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")


def _to_text(fragment: str) -> str:
    text = html.unescape(_TAGS.sub("", _BLOCK_TAGS.sub("\n", fragment))).replace(" ", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_ts(seg: str) -> datetime | None:
    m = _TIMESTAMP.match(html.unescape(seg).replace(" ", " ").strip())
    if not m:
        return None
    local = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d, %Y, %I:%M:%S %p")
    return local.replace(tzinfo=timezone(TZ_OFFSETS.get(m.group(3), timedelta(0))))


def _parse_cards(doc: str) -> list[tuple[datetime, str, str]]:
    """(timestamp, prompt, response) for every "Prompted" card."""
    turns = []
    for card in doc.split('<div class="outer-cell')[1:]:
        m = _CARD_BODY.search(card)
        if not m:
            continue
        segs = m.group(1).split("<br>")
        ts_idx = next((i for i, s in enumerate(segs) if _parse_ts(s)), None)
        if ts_idx is None:
            continue
        prompt = _to_text("<br>".join(segs[:ts_idx]))
        if not prompt.startswith("Prompted"):
            continue
        prompt = prompt.removeprefix("Prompted").strip()
        # Scheduled actions are recurring automated runs, not the user talking; they'd flood memory with repeats.
        if prompt and not prompt.startswith("<Automated scheduled action"):
            turns.append((_parse_ts(segs[ts_idx]), prompt, _to_text("<br>".join(segs[ts_idx + 1:]))))
    return sorted(turns, key=lambda t: t[0])


def _conversation(turns: list[tuple[datetime, str, str]]) -> Conversation:
    messages: list[Message] = []
    for ts, prompt, response in turns:
        key = ts.isoformat()
        append_message(messages, f"gemini:{key}:u", "user", prompt, key)
        append_message(messages, f"gemini:{key}:a", "assistant", response, key)
    return Conversation(
        id=f"gemini:{turns[0][0].isoformat()}",
        source="gemini",
        title=turns[0][1].splitlines()[0][:80],
        started_at=turns[0][0],
        ended_at=turns[-1][0],
        messages=messages,
    )


def parse_all(path: Path) -> list[Conversation]:
    turns = _parse_cards(Path(path).read_text(encoding="utf-8"))
    sessions: list[list] = []
    for turn in turns:
        if sessions and turn[0] - sessions[-1][-1][0] <= SESSION_GAP:
            sessions[-1].append(turn)
        else:
            sessions.append([turn])
    return [_conversation(s) for s in sessions]
