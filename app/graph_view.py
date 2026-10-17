"""Render a recall subgraph (nodes/edges dicts from mnemo.agent.recall) as interactive pyvis HTML."""

from pyvis.network import Network

COLORS = {
    "Me": "#e8590c",
    "Conversation": "#4c6ef5",
    "Topic": "#12b886",
    "Entity": "#20c997",
    "Context": "#7950f2",
    "Goal": "#f59f00",
    "Task": "#868e96",
    "Preference": "#e64980",
    "Domain": "#1c7ed6",
}
SIZES = {"Me": 28, "Goal": 20, "Preference": 18, "Conversation": 18, "Domain": 16}


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
