"""Email → graph (no LLM). Threads, emails, people, orgs, and per-person relationship stats from real behaviour."""

from neo4j import Driver

from mnemo.gmail.models import FREEMAIL, EmailThread
from mnemo.graph.db import database

WRITE_THREADS = """
UNWIND $threads AS t
MERGE (th:Thread {id: t.id})
SET th.subject = t.subject, th.last_at = t.last_at, th.snippet = t.snippet, th.labels = t.labels,
    th.unread = t.unread, th.in_inbox = t.in_inbox, th.is_bulk = t.is_bulk, th.message_count = size(t.messages)
WITH th, t
UNWIND t.messages AS m
MERGE (e:Email {id: m.id})
SET e.at = m.at, e.subject = m.subject, e.snippet = m.snippet, e.labels = m.labels,
    e.message_id = m.message_id, e.references = m.references, e.is_bulk = m.is_bulk
MERGE (th)-[:HAS_EMAIL]->(e)
WITH e, m
CALL (e, m) {
  MERGE (p:Person {email: m.sender.email})
  SET p.name = coalesce(m.sender.name, p.name), p.domain = m.sender.domain
  MERGE (e)-[:FROM]->(p)
}
CALL (e, m) {
  UNWIND m.to AS a
  MERGE (p:Person {email: a.email})
  SET p.name = coalesce(p.name, a.name), p.domain = a.domain
  MERGE (e)-[:TO]->(p)
}
CALL (e, m) {
  UNWIND m.cc AS a
  MERGE (p:Person {email: a.email})
  SET p.name = coalesce(p.name, a.name), p.domain = a.domain
  MERGE (e)-[:CC]->(p)
}
"""

MARK_SELF = """
MATCH (me:Me {id: 'me'})
MERGE (s:Person {email: $email})
SET s:Self
MERGE (me)-[:IS]->(s)
"""

LINK_ORGS = """
MATCH (p:Person)
WHERE NOT p:Self AND p.domain IS NOT NULL AND NOT p.domain IN $freemail
MERGE (o:Org {domain: p.domain})
MERGE (p)-[:WORKS_AT]->(o)
"""

MARK_BULK = """
MATCH (p:Person)
SET p.is_bulk = EXISTS { (e:Email {is_bulk: true})-[:FROM]->(p) }
"""

# How the user actually treats each person, computed from the graph:
#   answered = their emails you replied to later in the same thread; avg_reply_hours = how fast.
#   human_share = share of their emails Gmail filed as Important/Personal (vs Updates/Promotions).
#   met = past calendar events with them (C2), so people you meet but rarely email still count.
#   importance mixes reply behaviour, how much you write to them, human_share, recency, meetings and UI feedback.
CORRESPONDENCE_STATS = """
MATCH (:Me)-[stale:CORRESPONDS_WITH]->(:Self)
DELETE stale
WITH count(*) AS _
MATCH (me:Me {id: 'me'})
MATCH (p:Person) WHERE NOT p:Self
CALL (p) {
  OPTIONAL MATCH (t:Thread)-[:HAS_EMAIL]->(e:Email)-[:FROM]->(p)
  OPTIONAL MATCH (t)-[:HAS_EMAIL]->(r:Email)-[:FROM]->(:Self) WHERE r.at > e.at
  WITH e, min(r.at) AS reply_at
  RETURN count(e) AS received, count(reply_at) AS answered, max(e.at) AS last_in,
         count(CASE WHEN 'IMPORTANT' IN e.labels OR 'CATEGORY_PERSONAL' IN e.labels THEN 1 END) AS human,
         avg(CASE WHEN reply_at IS NULL THEN null
                  ELSE duration.inSeconds(e.at, reply_at).seconds / 3600.0 END) AS avg_reply_hours
}
CALL (p) {
  OPTIONAL MATCH (e:Email)-[:FROM]->(:Self) WHERE EXISTS { (e)-[:TO|CC]->(p) }
  RETURN count(DISTINCT e) AS sent, max(e.at) AS last_out
}
CALL (p) {
  OPTIONAL MATCH (ev:Event)-[a:ATTENDED_BY]->(p) WHERE ev.start < datetime() AND a.response <> 'declined'
  RETURN count(ev) AS met, max(ev.start) AS last_met
}
WITH me, p, received, answered, avg_reply_hours, sent, human, met, last_met,
     reduce(m = null, d IN [last_in, last_out, last_met] | CASE WHEN m IS NULL OR d > m THEN d ELSE m END) AS last
WHERE received + sent + met > 0
MERGE (me)-[c:CORRESPONDS_WITH]->(p)
SET c.received = received, c.sent = sent, c.answered = answered, c.avg_reply_hours = avg_reply_hours, c.last = last,
    c.met = met, c.last_met = last_met,
    c.reply_rate = CASE WHEN received = 0 THEN null ELSE toFloat(answered) / received END,
    c.human_share = CASE WHEN received = 0 THEN 1.0 ELSE toFloat(human) / received END
WITH c, p, duration.inDays(c.last, datetime()).days AS age_days
SET c.importance = CASE WHEN p.is_bulk THEN 0.05 ELSE
      0.35 * coalesce(c.reply_rate, CASE WHEN c.sent > 0 THEN 1.0 ELSE 0.0 END)
    + 0.20 * (CASE WHEN c.sent > 10 THEN 10 ELSE c.sent END) / 10.0
    + 0.20 * c.human_share
    + 0.15 * exp(-age_days / 30.0)
    + 0.10 * (CASE WHEN c.met > 5 THEN 5 ELSE c.met END) / 5.0
    + 0.10 END + coalesce(c.feedback_boost, 0.0)
"""

SUMMARY = """
CALL () {
  MATCH (n) WHERE n:Thread OR n:Email OR n:Person OR n:Org OR n:Event RETURN labels(n)[0] AS k, count(*) AS n
  UNION ALL
  MATCH (:Me)-[c:CORRESPONDS_WITH]->() RETURN 'CORRESPONDS_WITH' AS k, count(c) AS n
}
RETURN k, n ORDER BY k
"""

TOP_CONTACTS = """
MATCH (:Me {id: 'me'})-[c:CORRESPONDS_WITH]->(p:Person)
RETURN coalesce(p.name, p.email) AS who, p.domain AS domain, round(c.importance, 2) AS importance,
       c.received AS received, c.sent AS sent, round(c.reply_rate, 2) AS reply_rate,
       round(c.avg_reply_hours, 1) AS avg_reply_hours
ORDER BY c.importance DESC
LIMIT $limit
"""

THREAD_BATCH = 50


def write_threads(driver: Driver, threads: list[EmailThread], self_emails: list[str]) -> None:
    for i in range(0, len(threads), THREAD_BATCH):
        driver.execute_query(WRITE_THREADS, threads=[t.to_graph() for t in threads[i:i + THREAD_BATCH]],
                             database_=database())
    recompute(driver, self_emails)


def recompute(driver: Driver, self_emails: list[str]) -> None:
    """Re-derive Self, orgs, bulk flags and relationship stats from what's already in the graph."""
    for email in self_emails:
        driver.execute_query(MARK_SELF, email=email.lower(), database_=database())
    driver.execute_query("MATCH (s:Self)-[r:WORKS_AT]->() DELETE r", database_=database())
    driver.execute_query(LINK_ORGS, freemail=sorted(FREEMAIL), database_=database())
    driver.execute_query(MARK_BULK, database_=database())
    driver.execute_query(CORRESPONDENCE_STATS, database_=database())


def summary(driver: Driver) -> list[dict]:
    return [r.data() for r in driver.execute_query(SUMMARY, database_=database())[0]]


def top_contacts(driver: Driver, limit: int = 10) -> list[dict]:
    return [r.data() for r in driver.execute_query(TOP_CONTACTS, limit=limit, database_=database())[0]]


# --- G3: thread extraction ------------------------------------------------------

# Threads worth reading in full: not bulk, recent, ranked by how much the user cares about the people in them.
THREADS_TO_EXTRACT = """
MATCH (th:Thread)-[:HAS_EMAIL]->(:Email)-[:FROM|TO|CC]->(p:Person)
WHERE NOT th.is_bulk AND th.last_at > datetime() - duration({days: $days})
OPTIONAL MATCH (:Me)-[c:CORRESPONDS_WITH]->(p)
WITH th, max(coalesce(c.importance, 0.0)) AS importance
RETURN th.id AS id, th.subject AS subject, importance
ORDER BY importance DESC, th.last_at DESC
LIMIT $limit
"""

CONTEXT_NAMES = "MATCH (c:Context) RETURN c.name AS name ORDER BY name"

CLEAR_THREAD_EXTRACTION = """
MATCH (th:Thread {id: $id})
OPTIONAL MATCH (th)-[r:ABOUT|IN_CONTEXT|RELATES_TO]->()
DELETE r
WITH DISTINCT th
OPTIONAL MATCH (th)-[:HAS_COMMITMENT]->(c:Commitment)
DETACH DELETE c
"""

WRITE_THREAD_EXTRACTION = """
MATCH (th:Thread {id: $id})
SET th.summary = $x.summary, th.category = $x.category, th.needs_reply = $x.needs_reply,
    th.urgency = $x.urgency, th.extracted_at = datetime()
FOREACH (t IN $x.topics | MERGE (tp:Topic {name: t}) MERGE (th)-[:ABOUT]->(tp))
FOREACH (i IN range(0, size($x.commitments) - 1) |
  MERGE (c:Commitment {id: $id + ':' + i})
  SET c.text = $x.commitments[i].text, c.owner = $x.commitments[i].owner,
      c.due = CASE WHEN $x.commitments[i].due IS NULL THEN null ELSE date($x.commitments[i].due) END,
      c.status = $x.commitments[i].status
  MERGE (th)-[:HAS_COMMITMENT]->(c))
WITH th
OPTIONAL MATCH (ctx:Context {name: $x.context})
FOREACH (_ IN CASE WHEN ctx IS NULL THEN [] ELSE [1] END | MERGE (th)-[:IN_CONTEXT]->(ctx))
WITH th
OPTIONAL MATCH (g:Goal {id: $x.goal_id})
FOREACH (_ IN CASE WHEN g IS NULL THEN [] ELSE [1] END | MERGE (th)-[:RELATES_TO]->(g))
WITH th
// "them" commitments are owed by the other people in the thread; "me" ones by the user
MATCH (th)-[:HAS_COMMITMENT]->(c:Commitment)
CALL (th, c) {
  WITH th, c WHERE c.owner = 'them'
  MATCH (th)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
  WITH c, p LIMIT 1
  MERGE (c)-[:OWED_BY]->(p)
}
CALL (c) {
  WITH c WHERE c.owner = 'me'
  MATCH (me:Me {id: 'me'})
  MERGE (c)-[:OWED_BY]->(me)
}
"""


def threads_to_extract(driver: Driver, days: int, limit: int) -> list[dict]:
    return [r.data() for r in driver.execute_query(THREADS_TO_EXTRACT, days=days, limit=limit,
                                                   database_=database())[0]]


def context_names(driver: Driver) -> list[str]:
    return [r["name"] for r in driver.execute_query(CONTEXT_NAMES, database_=database())[0]]


def write_thread_extraction(driver: Driver, thread_id: str, x) -> None:
    def work(tx):
        tx.run(CLEAR_THREAD_EXTRACTION, id=thread_id).consume()
        tx.run(WRITE_THREAD_EXTRACTION, id=thread_id, x=x.model_dump()).consume()

    with driver.session(database=database()) as session:
        session.execute_write(work)
