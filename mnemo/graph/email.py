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
SET s:Self, me.email = $email
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
#   importance mixes reply rate, how much you write to them, recency, and feedback from the UI (G5).
CORRESPONDENCE_STATS = """
MATCH (me:Me {id: 'me'})-[:IS]->(self:Self)
MATCH (p:Person) WHERE NOT p:Self
CALL (p, self) {
  OPTIONAL MATCH (t:Thread)-[:HAS_EMAIL]->(e:Email)-[:FROM]->(p)
  OPTIONAL MATCH (t)-[:HAS_EMAIL]->(r:Email)-[:FROM]->(self) WHERE r.at > e.at
  WITH e, min(r.at) AS reply_at
  RETURN count(e) AS received, count(reply_at) AS answered, max(e.at) AS last_in,
         avg(CASE WHEN reply_at IS NULL THEN null
                  ELSE duration.inSeconds(e.at, reply_at).seconds / 3600.0 END) AS avg_reply_hours
}
CALL (p, self) {
  OPTIONAL MATCH (e:Email)-[:FROM]->(self) WHERE EXISTS { (e)-[:TO|CC]->(p) }
  RETURN count(e) AS sent, max(e.at) AS last_out
}
WITH me, p, received, answered, avg_reply_hours, sent,
     CASE WHEN last_in IS NULL OR (last_out IS NOT NULL AND last_out > last_in) THEN last_out ELSE last_in END AS last
WHERE received + sent > 0
MERGE (me)-[c:CORRESPONDS_WITH]->(p)
SET c.received = received, c.sent = sent, c.answered = answered, c.avg_reply_hours = avg_reply_hours, c.last = last,
    c.reply_rate = CASE WHEN received = 0 THEN null ELSE toFloat(answered) / received END
WITH c, p, duration.inDays(c.last, datetime()).days AS age_days
SET c.importance = CASE WHEN p.is_bulk THEN 0.05 ELSE
      0.45 * coalesce(c.reply_rate, CASE WHEN c.sent > 0 THEN 1.0 ELSE 0.0 END)
    + 0.25 * (CASE WHEN c.sent > 10 THEN 10 ELSE c.sent END) / 10.0
    + 0.20 * exp(-age_days / 30.0)
    + 0.10 END + coalesce(c.feedback_boost, 0.0)
"""

SUMMARY = """
CALL () {
  MATCH (n) WHERE n:Thread OR n:Email OR n:Person OR n:Org RETURN labels(n)[0] AS k, count(*) AS n
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


def write_threads(driver: Driver, threads: list[EmailThread], self_email: str) -> None:
    driver.execute_query(MARK_SELF, email=self_email, database_=database())
    for i in range(0, len(threads), THREAD_BATCH):
        driver.execute_query(WRITE_THREADS, threads=[t.to_graph() for t in threads[i:i + THREAD_BATCH]],
                             database_=database())
    driver.execute_query(MARK_SELF, email=self_email, database_=database())  # self may now have a name
    driver.execute_query(LINK_ORGS, freemail=sorted(FREEMAIL), database_=database())
    driver.execute_query(MARK_BULK, database_=database())
    driver.execute_query(CORRESPONDENCE_STATS, database_=database())


def summary(driver: Driver) -> list[dict]:
    return [r.data() for r in driver.execute_query(SUMMARY, database_=database())[0]]


def top_contacts(driver: Driver, limit: int = 10) -> list[dict]:
    return [r.data() for r in driver.execute_query(TOP_CONTACTS, limit=limit, database_=database())[0]]
