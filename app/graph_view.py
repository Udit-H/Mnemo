"""Render a recall subgraph (nodes/edges dicts from mnemo.agent.recall) as interactive pyvis HTML."""

from pyvis.network import Network

COLORS = {
    "Me": "#e8590c",
    "Thread": "#364fc7",
    "Person": "#0ca678",
    "Org": "#5c940d",
    "Commitment": "#d6336c",
    "Conversation": "#4c6ef5",
    "Topic": "#12b886",
    "Entity": "#20c997",
    "Context": "#7950f2",
    "Goal": "#f59f00",
    "Task": "#868e96",
    "Preference": "#e64980",
    "Domain": "#1c7ed6",
}
SIZES = {"Me": 28, "Thread": 24, "Goal": 20, "Preference": 18, "Conversation": 16, "Domain": 16, "Person": 18}


def thread_graph(nb: dict) -> tuple[list[dict], list[dict]]:
    """Nodes/edges for one thread's neighbourhood (THREAD_NEIGHBORHOOD row)."""
    nodes = {"me": {"id": "me", "label": "Me", "name": "Me"},
             nb["id"]: {"id": nb["id"], "label": "Thread", "name": nb["subject"]}}
    edges = []

    def add(nid, label, name, source, target, rel):
        nodes.setdefault(nid, {"id": nid, "label": label, "name": name})
        edges.append({"source": source, "target": target, "type": rel})

    for p in nb["people"]:
        pid = f"p:{p['email']}"
        add(pid, "Person", p["name"] or p["email"], nb["id"], pid, "INVOLVES")
        if p["importance"] is not None:
            edges.append({"source": "me", "target": pid, "type": f"CORRESPONDS_WITH {p['importance']}"})
        if p["org"]:
            add(f"o:{p['org']}", "Org", p["org"], pid, f"o:{p['org']}", "WORKS_AT")
    for c in nb["commitments"]:
        add(c["id"], "Commitment", f"{c['text']} ({c['status']})", nb["id"], c["id"], "HAS_COMMITMENT")
        owner = "me" if c["owner"] == "me" else f"p:{c['by']}"
        if owner in nodes:
            edges.append({"source": c["id"], "target": owner, "type": "OWED_BY"})
    for t in nb["topics"]:
        add(f"t:{t}", "Topic", t, nb["id"], f"t:{t}", "ABOUT")
    for x in nb["projects"]:
        add(f"x:{x}", "Context", x, nb["id"], f"x:{x}", "IN_CONTEXT")
        for chat in nb["related_chats"]:
            add(f"c:{chat}", "Conversation", chat, f"c:{chat}", f"x:{x}", "IN_CONTEXT")
    for g in nb["goals"]:
        add(f"g:{g}", "Goal", g, nb["id"], f"g:{g}", "RELATES_TO")
    return list(nodes.values()), edges


def _short(text: str, n: int = 32) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def render(nodes: list[dict], edges: list[dict], height: int = 560) -> str:
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#ffffff", font_color="#212529",
                  cdn_resources="remote")
    for n in nodes:
        net.add_node(n["id"], label=_short(n["name"]), title=f"{n['label']}: {n['name']}",
                     color=COLORS.get(n["label"], "#adb5bd"), size=SIZES.get(n["label"], 12), shape="dot")
    for e in edges:
        net.add_edge(e["source"], e["target"], label=e["type"], title=e["type"], arrows="to",
                     color="#ced4da", font={"size": 9, "color": "#868e96", "strokeWidth": 0})
    net.set_options("""{
      "physics": {"barnesHut": {"gravitationalConstant": -6000, "springLength": 140}, "stabilization": {"iterations": 150}},
      "interaction": {"hover": true, "tooltipDelay": 120},
      "nodes": {"font": {"size": 13}}
    }""")
    return net.generate_html()


def legend_html() -> str:
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;margin:0 10px 4px 0;font-size:12px">'
        f'<span style="width:10px;height:10px;border-radius:50%;background:{c};margin-right:4px"></span>{k}</span>'
        for k, c in COLORS.items()
    )
    return f"<div>{chips}</div>"
