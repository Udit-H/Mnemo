"""Mnemo UI. Run from the repo root:  streamlit run app/streamlit_app.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app")]

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from graph_view import legend_html, render  # noqa: E402
from mnemo.agent.agent import Agent  # noqa: E402
from mnemo.graph.db import database, get_driver  # noqa: E402
from mnemo.graph.insights import DOMAIN_TIMELINE, GRAPH_STATS, INSIGHTS  # noqa: E402
from mnemo.graph.writer import forget_source  # noqa: E402
from mnemo.llm import GroqPool  # noqa: E402

st.set_page_config(page_title="Mnemo · graph memory", page_icon="🧠", layout="wide")


@st.cache_resource
def driver():
    return get_driver()


@st.cache_resource
def llm():
    return GroqPool.from_env()


def query(cypher: str, **params) -> list[dict]:
    records, _, _ = driver().execute_query(cypher, params, database_=database())
    return [r.data() for r in records]


def agent() -> Agent:
    if "agent" not in st.session_state:
        st.session_state.agent = Agent(driver=driver(), llm=llm())
        st.session_state.turns = []
    return st.session_state.agent


# --- Sidebar -------------------------------------------------------------------
with st.sidebar:
    st.title("🧠 Mnemo")
    st.caption("A personal AI agent whose long-term memory is a Neo4j knowledge graph.")
    remember = st.toggle("Remember this chat", value=True,
                         help="Write each turn back into the graph (extraction runs after every answer).")
    show_context = st.toggle("Show recalled memory text", value=False)
    st.divider()
    st.subheader("Memory graph")
    for row in query(GRAPH_STATS):
        st.markdown(f"**{row['n']:,}** {row['k']}")
    st.divider()
    if st.button("New chat session", width="stretch"):
        st.session_state.pop("agent", None)
        st.rerun()
    if st.button("Forget all live sessions", width="stretch",
                 help="Delete every chat made in this app and what Mnemo learned from them (use after rehearsals)."):
        n = forget_source(driver(), "live")
        st.session_state.pop("agent", None)
        st.toast(f"Forgot live sessions ({n} nodes deleted)")
        st.rerun()

chat_tab, insights_tab = st.tabs(["💬 Chat", "📊 Insights"])

# --- Chat ----------------------------------------------------------------------
with chat_tab:
    a = agent()
    turns = st.session_state.turns
    left, right = st.columns([5, 6], gap="large")

    with left:
        st.caption(f"Session `{a.session_id}`")
        box = st.container(height=600)
        for t in turns:
            box.chat_message("user").markdown(t["user"])
            msg = box.chat_message("assistant", avatar="🧠")
            msg.markdown(t["answer"])
            if t["remembered"]:
                msg.caption("⟡ remembered: " + t["remembered"])
            elif t["memory_error"]:
                msg.caption("⟡ memory not saved: " + t["memory_error"])

        if prompt := st.chat_input("Ask Mnemo anything…"):
            box.chat_message("user").markdown(prompt)
            with box.chat_message("assistant", avatar="🧠"):
                with st.spinner("Recalling from the graph, thinking" + (", then remembering…" if remember else "…")):
                    turn = a.chat(prompt, remember=remember)
            x = turn.remembered
            remembered = None
            if x:
                bits = [f"preference “{p.text}”" for p in x.preferences] + [f"goal “{g.text}”" for g in x.goals]
                remembered = "; ".join(bits) or ("topics: " + ", ".join(x.topics) if x.topics else "nothing new")
            r = turn.recall
            turns.append({
                "user": prompt, "answer": turn.answer, "remembered": remembered, "memory_error": turn.memory_error,
                "nodes": r.nodes, "edges": r.edges, "context": r.context,
                "counts": (len(r.conversations), len(r.goals), len(r.preferences)),
                "seeds": [(s["label"], s["name"]) for s in r.seeds],
            })
            st.rerun()

    with right:
        if not turns:
            st.subheader("Memory used")
            st.info("Ask something. This panel shows the part of your memory graph Mnemo traversed to answer.")
        else:
            labels = [f"{i + 1}. {t['user'][:50]}" for i, t in enumerate(turns)]
            pick = st.selectbox("Memory used for", labels, index=len(labels) - 1, label_visibility="collapsed")
            t = turns[labels.index(pick)]
            convs, goals, prefs = t["counts"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Conversations", convs)
            c2.metric("Goals", goals)
            c3.metric("Preferences", prefs)
            c4.metric("Graph nodes", len(t["nodes"]))
            if t["nodes"]:
                components.html(render(t["nodes"], t["edges"]), height=575)
                st.markdown(legend_html(), unsafe_allow_html=True)
            if t["seeds"]:
                st.caption("Entry points matched in your message: " +
                           ", ".join(f"{label.lower()} **{name}**" for label, name in t["seeds"][:8]))
            if show_context:
                with st.expander("Recalled memory (exactly what the LLM saw)", expanded=True):
                    st.text(t["context"])

# --- Insights ------------------------------------------------------------------
with insights_tab:
    st.subheader("What your memory graph knows about you")
    timeline = pd.DataFrame(query(DOMAIN_TIMELINE))
    if not timeline.empty:
        st.markdown("**Conversations per domain over time**")
        st.bar_chart(timeline.pivot_table(index="month", columns="domain", values="conversations", fill_value=0))
    for title, explanation, cypher in INSIGHTS:
        st.markdown(f"#### {title}")
        st.caption(explanation)
        rows = query(cypher)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("_Nothing yet._")
        with st.expander("Cypher"):
            st.code(cypher, language="cypher")
