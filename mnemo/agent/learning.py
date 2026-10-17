"""Learn durable writing preferences from how the user edited a draft (G5)."""

import json

from mnemo.extract.schema import _pick
from mnemo.llm import GroqPool

PROMPT = """The assistant drafted an email reply; the user edited it before saving. Compare them.
What DURABLE writing preferences does the edit reveal about how the user writes to this recipient?
Only style that would apply to future emails: tone, formality, length, greeting, sign-off, language, emoji, bullet
points, directness. Ignore changes to facts or content specific to this one email.
Return JSON: {{"preferences": [{{"text": "short imperative, e.g. 'Sign off with Regards, Udit'", "scope": "person|org|everyone"}}]}}
scope: person = just this recipient ({recipient}); org = everyone at {org}; everyone = all emails.
Return an empty list if the edit only changed content.

ASSISTANT DRAFT:
{before}

USER'S VERSION:
{after}"""


def preferences_from_edit(llm: GroqPool, before: str, after: str, recipient: str) -> list[dict]:
    if before.strip() == after.strip():
        return []
    org = recipient.rsplit("@", 1)[-1]
    resp = llm.chat([{"role": "user", "content": PROMPT.format(before=before, after=after, recipient=recipient,
                                                               org=org)}],
                    model=llm.extract_model, response_format={"type": "json_object"}, temperature=0.1)
    try:
        raw = json.loads(resp.choices[0].message.content or "{}").get("preferences", [])
    except json.JSONDecodeError:
        return []
    return [{"text": p["text"].strip(), "scope": _pick(p.get("scope", "person"), ("person", "org", "everyone"),
                                                       "person")}
            for p in raw if isinstance(p, dict) and p.get("text", "").strip()][:4]
