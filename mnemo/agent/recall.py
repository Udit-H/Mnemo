"""Graph recall: seed from the message, expand through the graph, return LLM context + the subgraph used.

1. Seed   fulltext over memory names (Entity/Topic/Context/Goal/Preference) + vector match on Topic names.
2. Expand seeds → conversations (ABOUT/MENTIONS/IN_CONTEXT/FROM/USES), plus conversations whose past user
          messages are semantically close. Ranked by seed score and recency.
3. Gather for the top conversations: tasks → outcomes, goals they serve, and active preferences scoped to
          the same domains/contexts (or global).
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from neo4j import Driver

from mnemo.embed import embed
from mnemo.graph import queries as Q

MAX_CONVERSATIONS = 6
TOPIC_MIN_SCORE = 0.55
MESSAGE_MIN_SCORE = 0.6
RECENCY_HALF_LIFE_DAYS = 60

_STOPWORDS = set("""a an and are as at be but by can could do does for from had has have how i if in into is it its
me my no not of on or our please should so that the their them then there these they this to too us was we were what
when where which who why will with would you your about help tell give make need want know get just like""".split())
_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


@dataclass
class Recall:
    context: str  # text block for the LLM
    nodes: list[dict] = field(default_factory=list)  # {id, label, name}
    edges: list[dict] = field(default_factory=list)  # {source, target, type}
    seeds: list[dict] = field(default_factory=list)
    conversations: list[dict] = field(default_factory=list)
    preferences: list[dict] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.conversations or self.preferences or self.goals)


def _fulltext_query(text: str) -> str | None:
    words = [w for w in re.findall(r"[a-zA-Z0-9][\w.]+", _LUCENE_SPECIAL.sub(" ", text.lower()))
             if w not in _STOPWORDS and len(w) > 2]
    return " OR ".join(dict.fromkeys(words)) or None


def _recency(started_at) -> float:
    if started_at is None:
        return 0.0
    dt = started_at.to_native() if hasattr(started_at, "to_native") else started_at
    age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    return math.exp(-age_days * math.log(2) / RECENCY_HALF_LIFE_DAYS)


def recall(driver: Driver, message: str, exclude_conv_ids: list[str] | None = None) -> Recall:
    exclude = exclude_conv_ids or []
    vec = embed([message])[0]

    # 1. Seeds
    seeds: dict[str, dict] = {}
    if q := _fulltext_query(message):
        for s in Q.recall_rows(driver, Q.SEED_FULLTEXT, q=q):
            seeds[s["id"]] = {**s, "score": min(s["score"], 3.0) / 3.0}  # Lucene scores are unbounded
    for s in Q.recall_rows(driver, Q.SEED_TOPIC_VECTOR, vec=vec, min_score=TOPIC_MIN_SCORE):
        if s["id"] not in seeds or seeds[s["id"]]["score"] < s["score"]:
            seeds[s["id"]] = s

    # 2. Expand to conversations and rank
    ranked: dict[str, dict] = {}
    if seeds:
        for r in Q.recall_rows(driver, Q.SEED_CONVERSATIONS, seeds=list(seeds.values()), exclude=exclude):
            ranked[r["conv_id"]] = {"score": r["score"] * (0.5 + 0.5 * _recency(r["started_at"])), "via": r["via"]}
    for r in Q.recall_rows(driver, Q.MESSAGE_VECTOR_CONVERSATIONS, vec=vec, min_score=MESSAGE_MIN_SCORE,
                           exclude=exclude):
        entry = ranked.setdefault(r["conv_id"], {"score": 0.0, "via": []})
        entry["score"] += r["score"]
        entry["via"].append("similar_message")
    top_ids = [cid for cid, _ in sorted(ranked.items(), key=lambda kv: -kv[1]["score"])[:MAX_CONVERSATIONS]]

    # 3. Gather
    convs = Q.recall_rows(driver, Q.CONVERSATION_DETAILS, conv_ids=top_ids)
    convs.sort(key=lambda c: top_ids.index(c["id"]))
    scopes = sorted({x for c in convs for x in (c["domain"], c["context"]) if x})
    seed_ids = list(seeds)
    prefs = Q.recall_rows(driver, Q.RELEVANT_PREFERENCES, scopes=scopes, seed_ids=seed_ids)
    goals = Q.recall_rows(driver, Q.RELEVANT_GOALS, seed_ids=seed_ids, conv_ids=top_ids)

    result = Recall(context=_format(convs, prefs, goals), seeds=list(seeds.values()),
                    conversations=convs, preferences=prefs, goals=goals)
    _build_subgraph(result, ranked)
    return result


def _format(convs: list[dict], prefs: list[dict], goals: list[dict]) -> str:
    if not (convs or prefs or goals):
        return "(No relevant memories found.)"
    out = []
    if prefs:
        out.append("Active preferences (follow these unless the user says otherwise):")
        for p in prefs:
            where = ", ".join(p["scopes"]) or "everywhere"
            replaced = f"; replaced earlier: {'; '.join(p['replaced'])}" if p["replaced"] else ""
            out.append(f"- {p['text']}  [{where}, since {p['since']}{replaced}]")
    if goals:
        out.append("\nGoals the user is working towards:")
        out += [f"- {g['text']}  [{', '.join(g['domains'])}]" for g in goals]
    if convs:
        out.append("\nRelevant past conversations (most relevant first):")
        for c in convs:
            where = " / ".join(x for x in (c["domain"], c["context"]) if x)
            out.append(f"- {c['date']} · {c['source']} · {where} · \"{c['title']}\"")
            if c["summary"]:
                out.append(f"  Summary: {c['summary']}")
            for t in c["tasks"][:3]:
                out.append(f"  Task: {t['text']} → {t['outcome']}" + (f" ({t['summary']})" if t["summary"] else ""))
    return "\n".join(out)


def _build_subgraph(r: Recall, ranked: dict[str, dict]) -> None:
    nodes: dict[str, dict] = {"me": {"id": "me", "label": "Me", "name": "Me"}}
    edges: list[dict] = []

    def node(nid: str, label: str, name: str) -> None:
        nodes.setdefault(nid, {"id": nid, "label": label, "name": name})

    for s in r.seeds:
        node(s["id"], s["label"], s["name"])
    for c in r.conversations:
        node(c["id"], "Conversation", c["title"])
        for via in ranked.get(c["id"], {}).get("via", []):
            if via in nodes:
                edges.append({"source": c["id"], "target": via, "type": "ABOUT"})
        if c["domain"]:
            node(f"domain:{c['domain']}", "Domain", c["domain"])
            edges.append({"source": c["id"], "target": f"domain:{c['domain']}", "type": "IN_DOMAIN"})
        for t in c["tasks"][:3]:
            node(t["id"], "Task", t["text"])
            edges.append({"source": t["id"], "target": c["id"], "type": "FROM"})
            if t["goal_id"]:
                edges.append({"source": t["id"], "target": t["goal_id"], "type": "SERVES"})
    for g in r.goals:
        node(g["id"], "Goal", g["text"])
        edges.append({"source": "me", "target": g["id"], "type": "HAS_GOAL"})
    for p in r.preferences:
        node(p["id"], "Preference", p["text"])
        edges.append({"source": "me", "target": p["id"], "type": "PREFERS"})
    r.nodes = list(nodes.values())
    r.edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
