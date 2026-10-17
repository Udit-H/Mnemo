"""Mnemo PA. Run from the repo root:  streamlit run app/streamlit_app.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "app")]

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from graph_view import legend_html, render, thread_graph  # noqa: E402
from mnemo.agent.learning import preferences_from_edit  # noqa: E402
from mnemo.agent.pa_agent import PAAgent  # noqa: E402
from mnemo.agent.tools import ToolContext, compose_reply, save_draft  # noqa: E402
from mnemo.gmail.client import get_service, my_addresses  # noqa: E402
from mnemo.graph import feedback as fb  # noqa: E402
from mnemo.graph.db import database, get_driver  # noqa: E402
from mnemo.graph.insights import INSIGHTS  # noqa: E402
from mnemo.graph.pa import THREAD_NEIGHBORHOOD, TRIAGE  # noqa: E402
from mnemo.llm import GroqPool  # noqa: E402

st.set_page_config(page_title="Mnemo · PA", page_icon="🧠", layout="wide")
load_dotenv()


@st.cache_resource
def tool_ctx() -> ToolContext:
    gmail = get_service()
    return ToolContext(driver=get_driver(), llm=GroqPool.from_env(), gmail=gmail, self_emails=my_addresses(gmail))


def query(cypher: str, **params) -> list[dict]:
    records, _, _ = tool_ctx().driver.execute_query(cypher, params, database_=database())
    return [r.data() for r in records]


def reasons(t: dict) -> list[str]:
    """Why a thread ranks where it does, straight from the graph fields TRIAGE returned."""
    out = []
    who = t["people"][0] if t["people"] else "sender"
    if t["sender_importance"] >= 0.6 and t["your_reply_rate"]:
        out.append(f"you answer {who} {t['your_reply_rate']:.0%} of the time")
    elif t["sender_importance"] >= 0.6:
        out.append(f"{who} matters to you")
    if t["needs_reply"]:
        out.append("needs your reply")
    if t["urgency"] == "high":
        out.append("urgent")
    for c in t["you_owe"][:2]:
        out.append(f"you owe: {c['text'][:50]}" + (f" (due {c['due']}{', overdue' if c['overdue'] else ''})"
                                                     if c["due"] else ""))
    if t["linked_to"]:
        out.append("linked to " + ", ".join(x[:40] for x in t["linked_to"]))
    return out


ctx = tool_ctx()
st.session_state.setdefault("selected", None)
st.session_state.setdefault("drafts", {})
st.session_state.setdefault("chat", [])
if "agent" not in st.session_state:
    st.session_state.agent = PAAgent(ctx)

with st.sidebar:
    st.title("🧠 Mnemo")
    st.caption("Your PA on Gmail. It learns who matters, what you owe, and how you write, in a Neo4j graph.")
    st.caption(f"Signed in as {', '.join(ctx.self_emails)}")
    days = st.slider("Inbox window (days)", 7, 90, 30)
    st.divider()
    st.caption("Drafts are saved to Gmail Drafts only. Mnemo never sends email.")

inbox_tab, ask_tab, learned_tab, insights_tab = st.tabs(
    ["📥 Inbox", "💬 Ask Mnemo", "🧠 What Mnemo learned", "📊 Memory insights"])

# --- Inbox -------------------------------------------------------------------------
with inbox_tab:
    threads = query(TRIAGE, limit=15, days=days)
    left, right = st.columns([5, 7], gap="large")
    with left:
        st.subheader("Ranked by your graph")
        if not threads:
            st.info("Nothing to triage. Run `python -m scripts.gmail_sync` and `python -m scripts.email_extract`.")
        for t in threads:
            with st.container(border=True):
                top = st.columns([8, 2])
                top[0].markdown(f"**{t['subject'][:70]}**  \n{', '.join(t['people'][:2])} · {t['date']}")
                top[1].metric("score", t["score"], label_visibility="collapsed")
                for r in reasons(t):
                    st.caption("• " + r)
                b = st.columns(3)
                if b[0].button("Open", key=f"open{t['thread_id']}", width="stretch"):
                    st.session_state.selected = t["thread_id"]
                    st.rerun()
                if b[1].button("⬆ More like this", key=f"up{t['thread_id']}", width="stretch"):
                    changed = fb.prioritise_thread(ctx.driver, t["thread_id"])
                    st.toast("Learned: " + ", ".join(f"{c['person']} → {c['importance']}" for c in changed))
                    st.rerun()
                if b[2].button("✕ Dismiss", key=f"dn{t['thread_id']}", width="stretch"):
                    changed = fb.dismiss_thread(ctx.driver, t["thread_id"])
                    st.toast("Learned: " + ", ".join(f"{c['person']} → {c['importance']}" for c in changed))
                    st.rerun()

    with right:
        tid = st.session_state.selected or (threads[0]["thread_id"] if threads else None)
        rows = query(THREAD_NEIGHBORHOOD, id=tid) if tid else []
        if rows:
            nb = rows[0]
            st.subheader(nb["subject"])
            st.caption(f"{nb['category']} · urgency {nb['urgency']} · {nb['date']}"
                       + (" · **needs reply**" if nb["needs_reply"] else ""))
            if nb["summary"]:
                st.markdown(nb["summary"])

            nodes, edges = thread_graph(nb)
            components.html(render(nodes, edges, height=380), height=395)
            st.markdown(legend_html(), unsafe_allow_html=True)

            cols = st.columns(2)
            with cols[0]:
                st.markdown("**People**")
                for p in nb["people"]:
                    rr = f" · you reply {p['reply_rate']:.0%}" if p["reply_rate"] is not None else ""
                    hrs = f" in ~{p['avg_reply_hours']}h" if p["avg_reply_hours"] else ""
                    st.caption(f"{p['name'] or p['email']} ({p['org'] or 'personal'}) · importance "
                               f"{p['importance']}{rr}{hrs}")
                if nb["projects"] or nb["related_chats"]:
                    st.markdown("**Connected memory**")
                    for x in nb["projects"]:
                        st.caption(f"project **{x}** ← your AI chats: " + "; ".join(nb["related_chats"]))
            with cols[1]:
                st.markdown("**Commitments**")
                if not nb["commitments"]:
                    st.caption("None")
                for c in nb["commitments"]:
                    label = f"{'You' if c['owner'] == 'me' else 'They'}: {c['text']}" + (
                        f" (due {c['due']})" if c["due"] else "")
                    done = st.checkbox(label, value=c["status"] == "done", key=f"c{c['id']}")
                    if done != (c["status"] == "done"):
                        fb.set_commitment(ctx.driver, c["id"], done)
                        st.rerun()

            st.markdown("#### ✍️ Reply")
            instr = st.text_input("What should the reply say?", key=f"instr{tid}",
                                  placeholder="e.g. confirm I'll attend and ask for the venue")
            if st.button("Draft in my voice", key=f"gen{tid}"):
                with st.spinner("Drafting with what the graph knows about how you write to them…"):
                    st.session_state.drafts[tid] = compose_reply(ctx, tid, instr)
            draft = st.session_state.drafts.get(tid)
            if draft and "error" not in draft:
                used = [p for ps in draft["style_used"].values() if ps for p in ps]
                if used:
                    st.caption("Using your learned style: " + "; ".join(used))
                edited = st.text_area(f"To {draft['to']}. Edit freely; Mnemo learns from your edits.",
                                      draft["body"], height=220, key=f"body{tid}")
                if st.button("Save to Gmail Drafts", type="primary", key=f"save{tid}"):
                    with st.spinner("Saving and learning from your edits…"):
                        draft_id = save_draft(ctx, draft, edited)
                        learned = preferences_from_edit(ctx.llm, draft["body"], edited, draft["to"])
                        if learned:
                            fb.learn_preferences(ctx.driver, tid, draft["to"], learned, draft["body"], edited)
                    st.success(f"Saved to Gmail Drafts (id {draft_id}). Not sent.")
                    for p in learned:
                        st.info(f"🧠 Learned ({p['scope']}): {p['text']}")
            elif draft:
                st.error(draft["error"])

# --- Ask -----------------------------------------------------------------------------
with ask_tab:
    for m in st.session_state.chat:
        with st.chat_message(m["role"], avatar="🧠" if m["role"] == "assistant" else None):
            for s in m.get("steps", []):
                with st.expander(f"⚙ {s.tool}({s.args[:80]})"):
                    if s.cypher:
                        st.code(s.cypher.strip(), language="cypher")
                    if isinstance(s.result, list) and s.result and isinstance(s.result[0], dict):
                        st.dataframe(pd.DataFrame(s.result), hide_index=True, width="stretch")
                    else:
                        st.json(s.result, expanded=False)
            st.markdown(m["content"])
    if prompt := st.chat_input("Ask your PA… e.g. 'what should I focus on today?'"):
        st.session_state.chat.append({"role": "user", "content": prompt})
        with st.spinner("Querying your graph…"):
            turn = st.session_state.agent.chat(prompt)
        st.session_state.chat.append({"role": "assistant", "content": turn.answer, "steps": turn.steps})
        st.rerun()

# --- Learned ---------------------------------------------------------------------------
with learned_tab:
    st.subheader("How Mnemo has adapted to you")
    st.caption("Every action you take is a Feedback node in the graph and changes what Mnemo does next.")
    log = fb.feedback_log(ctx.driver)
    st.markdown("**Recent feedback**")
    st.dataframe(pd.DataFrame(log), hide_index=True, width="stretch") if log else st.caption("No feedback yet.")
    prefs = fb.scoped_preferences(ctx.driver)
    st.markdown("**How you write to specific people and orgs** (learned from your draft edits)")
    st.dataframe(pd.DataFrame(prefs), hide_index=True, width="stretch") if prefs else st.caption("None yet.")
    st.markdown("**Who matters to you** (from reply behaviour + your feedback)")
    st.dataframe(pd.DataFrame(query("""
        MATCH (:Me)-[c:CORRESPONDS_WITH]->(p:Person)
        RETURN coalesce(p.name, p.email) AS person, p.domain AS domain, round(c.importance, 2) AS importance,
               round(coalesce(c.feedback_boost, 0), 2) AS from_your_feedback, round(c.reply_rate, 2) AS reply_rate,
               round(c.avg_reply_hours, 1) AS avg_reply_hours, c.received AS received, c.sent AS sent
        ORDER BY c.importance DESC LIMIT 15""")), hide_index=True, width="stretch")

# --- Insights (v1 memory) ----------------------------------------------------------------
with insights_tab:
    st.caption("Long-term memory from your AI chats (Claude Code + Gemini), which your email links into.")
    for title, explanation, cypher in INSIGHTS:
        st.markdown(f"#### {title}")
        st.caption(explanation)
        rows = query(cypher)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("_Nothing yet._")
        with st.expander("Cypher"):
            st.code(cypher, language="cypher")
