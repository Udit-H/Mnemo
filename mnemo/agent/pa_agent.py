"""The PA agent: an LLM tool loop over the graph. Every step is traced (tool, args, Cypher, result) for the UI."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mnemo.agent import tools
from mnemo.agent.tools import ToolContext

MAX_STEPS = 6
HISTORY_TURNS = 6

SYSTEM_PROMPT = """You are Mnemo, the user's personal assistant (PA). Today is {today}. The user is {me}.
You work on their Gmail and a Neo4j graph that models them: who they care about (from how they actually reply),
what they owe people, their projects and goals (from their past AI chats), and how they like to write.

How to work:
- Use tools to look things up; never guess about emails, people or dates. Prefer the specific tools; use run_cypher
  only for questions they don't cover.
- When you prioritise or recommend, give the graph evidence briefly and only as the tool results state it (e.g. a
  0.0 reply rate means the user does NOT usually reply) ("you reply to her within 2h", "you promised this
  by Friday", "linked to your Neo4J-Hack project").
- Drafts: use draft_reply, which saves to Gmail Drafts. You can never send email; say the draft is ready to review.
- Be concise, like a sharp human PA: what matters, why, what to do next."""


@dataclass
class Step:
    tool: str
    args: str
    cypher: str
    result: Any


@dataclass
class PATurn:
    answer: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class PAAgent:
    ctx: ToolContext
    history: list[dict] = field(default_factory=list)

    def chat(self, user_text: str) -> PATurn:
        system = SYSTEM_PROMPT.format(today=date.today().isoformat(), me=", ".join(self.ctx.self_emails))
        messages = [{"role": "system", "content": system}, *self.history[-2 * HISTORY_TURNS:],
                    {"role": "user", "content": user_text}]
        steps: list[Step] = []
        answer = ""
        for _ in range(MAX_STEPS):
            resp = self.ctx.llm.chat(messages, model=self.ctx.llm.agent_model, tools=tools.SPECS,
                                     tool_choice="auto", temperature=0.3)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                answer = (msg.content or "").strip()
                break
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name,
                                                               "arguments": tc.function.arguments}}
                for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                payload, result, cypher = tools.call(self.ctx, tc.function.name, tc.function.arguments)
                steps.append(Step(tc.function.name, tc.function.arguments, cypher, result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": payload})
        else:
            answer = "I ran out of steps before finishing. Try a narrower question."

        self.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
        return PATurn(answer=answer, steps=steps)
