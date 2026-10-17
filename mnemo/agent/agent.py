"""The Mnemo agent: recall from the graph → answer → remember the exchange back into the graph.

Each chat session is stored as a `live` Conversation and re-extracted after every turn with the same pipeline
as imported chats (M2). A correction or new preference is therefore in the graph before the next message.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from neo4j import Driver

from mnemo.agent.recall import Recall, recall
from mnemo.embed import embed_missing
from mnemo.extract.extractor import extract
from mnemo.extract.schema import Extraction
from mnemo.graph.queries import active_preferences, existing_goals, top_topics
from mnemo.graph.writer import link_topics, write_conversations, write_extraction
from mnemo.ingest.models import MAX_MESSAGE_CHARS, Conversation, Message
from mnemo.llm import GroqPool

HISTORY_TURNS = 8
RECALL_USER_TURNS = 2  # earlier user messages added to the recall query, so follow-ups ("no, use X") keep context

SYSTEM_PROMPT = """You are Mnemo, the user's personal AI assistant. You have long-term memory: a knowledge graph built
from the user's past conversations with AI assistants (coding sessions, study, career, planning, personal life).

Below is what you recalled for this message. How to use it:
- Use memory when it is relevant; ignore it when it isn't. Never invent memories.
- When a memory shapes your answer, say so briefly and naturally ("Last time you tried X it failed because Y, so…",
  "You prefer compact diagrams, so…"). One short mention is enough. Don't list everything you remember.
- Follow the active preferences unless the user overrides them in this conversation.
- Build on past outcomes: reuse what worked, avoid what failed.
- Be concise and direct.

=== MEMORY ===
{memory}
=== END MEMORY ==="""


@dataclass
class Turn:
    answer: str
    recall: Recall
    remembered: Extraction | None = None
    memory_error: str | None = None


@dataclass
class Agent:
    driver: Driver
    llm: GroqPool
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[Message] = field(default_factory=list)

    @property
    def conversation_id(self) -> str:
        return f"live:{self.session_id}"

    def chat(self, user_text: str, remember: bool = True) -> Turn:
        recent = [m.text for m in self.messages if m.role == "user"][-RECALL_USER_TURNS:]
        mem = recall(self.driver, "\n".join([*recent, user_text]), exclude_conv_ids=[self.conversation_id])
        history = [{"role": m.role, "content": m.text} for m in self.messages[-2 * HISTORY_TURNS:]]
        resp = self.llm.chat(
            [{"role": "system", "content": SYSTEM_PROMPT.format(memory=mem.context)}, *history,
             {"role": "user", "content": user_text}],
            model=self.llm.agent_model,
            temperature=0.5,
        )
        answer = (resp.choices[0].message.content or "").strip()

        now = datetime.now(timezone.utc)
        for role, text in (("user", user_text), ("assistant", answer)):
            self.messages.append(Message(id=f"{self.conversation_id}:{len(self.messages)}", role=role,
                                         text=text[:MAX_MESSAGE_CHARS], ts=now))
        turn = Turn(answer=answer, recall=mem)
        if remember:
            try:
                turn.remembered = self.remember()
            except Exception as e:  # memory write-back must never break the chat
                turn.memory_error = f"{type(e).__name__}: {e}"
        return turn

    def remember(self) -> Extraction:
        """Store this session as a Conversation, extract memory from it, embed anything new."""
        first = next(m.text for m in self.messages if m.role == "user")
        conv = Conversation(id=self.conversation_id, source="live", title=first.splitlines()[0][:80],
                            started_at=self.started_at, ended_at=self.messages[-1].ts, messages=self.messages)
        write_conversations(self.driver, [conv])

        goals, prefs = existing_goals(self.driver), active_preferences(self.driver)
        conv_dict = {"id": conv.id, "title": conv.title,
                     "messages": [{"role": m.role, "text": m.text, "idx": i} for i, m in enumerate(self.messages)]}
        x, _ = extract(self.llm, conv_dict, goals, prefs, top_topics(self.driver), force=True)
        write_extraction(self.driver, conv.id, x, {g["id"] for g in goals}, {p["id"] for p in prefs})
        link_topics(self.driver)
        embed_missing(self.driver)
        return x
