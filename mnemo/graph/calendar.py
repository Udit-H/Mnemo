"""Calendar → graph (no LLM). Events, who attends them, and links to the email threads and projects behind them."""

from datetime import datetime

from neo4j import Driver

from mnemo.calendar.client import CalEvent
from mnemo.graph.db import database

WRITE_EVENTS = """
MATCH (me:Me {id: 'me'})
UNWIND $events AS ev
MERGE (e:Event {id: ev.id})
SET e.title = ev.title, e.start = ev.start, e.end = ev.end, e.all_day = ev.all_day, e.location = ev.location,
    e.description = ev.description, e.status = ev.status, e.link = ev.link, e.meet_link = ev.meet_link,
    e.recurring_id = ev.recurring_id
MERGE (me)-[:HAS_EVENT]->(e)
WITH e, ev
CALL (e) {
  MATCH (e)-[old:ATTENDED_BY|DISCUSSED_IN|IN_CONTEXT]->()
  DELETE old
}
CALL (e, ev) {
  UNWIND ev.attendees AS a
  MERGE (p:Person {email: a.email})
  SET p.name = coalesce(p.name, a.name), p.domain = a.domain
  MERGE (e)-[r:ATTENDED_BY]->(p)
  SET r.response = a.response, r.organizer = a.organizer
}
"""

# Events in the synced window that Google no longer returns were cancelled or deleted.
PRUNE_EVENTS = """
MATCH (e:Event)
WHERE e.start >= $start AND e.start <= $end AND NOT e.id IN $ids
DETACH DELETE e
"""

# The email behind a meeting: threads with an attendee active in the 30 days before it (up to 2 days after),
# or whose subject mentions the event title. Then the event inherits those threads' projects.
LINK_THREADS = """
MATCH (e:Event) WHERE e.id IN $ids
CALL (e) {
  MATCH (e)-[:ATTENDED_BY]->(p:Person)<-[:FROM|TO|CC]-(:Email)<-[:HAS_EMAIL]-(th:Thread)
  WHERE NOT th.is_bulk AND NOT p.is_bulk
    AND th.last_at >= e.start - duration({days: 30}) AND th.last_at <= e.start + duration({days: 2})
  RETURN th
  UNION
  MATCH (th:Thread)
  WHERE size(e.title) >= 6 AND toLower(th.subject) CONTAINS toLower(e.title)
  RETURN th
}
WITH e, th ORDER BY th.last_at DESC
WITH e, collect(th)[..3] AS threads
FOREACH (th IN threads | MERGE (e)-[:DISCUSSED_IN]->(th))
WITH e
MATCH (e)-[:DISCUSSED_IN]->(:Thread)-[:IN_CONTEXT]->(c:Context)
MERGE (e)-[:IN_CONTEXT]->(c)
"""

SUMMARY = """
OPTIONAL MATCH (e:Event)
RETURN count(e) AS events,
       count(CASE WHEN e.start > datetime() THEN 1 END) AS upcoming,
       COUNT { (:Event)-[:ATTENDED_BY]->(:Person) } AS attendances,
       COUNT { (:Event)-[:DISCUSSED_IN]->(:Thread) } AS thread_links,
       COUNT { (:Event)-[:IN_CONTEXT]->(:Context) } AS project_links
"""

UPCOMING = """
MATCH (:Me {id: 'me'})-[:HAS_EVENT]->(e:Event)
WHERE e.end >= datetime() AND e.start <= datetime() + duration({days: $days})
RETURN e.id AS id, e.title AS title, e.start AS start, e.end AS end, e.all_day AS all_day,
       [(e)-[:ATTENDED_BY]->(p:Person) | coalesce(p.name, p.email)] AS attendees,
       [(e)-[:DISCUSSED_IN]->(th:Thread) | th.subject] AS threads,
       [(e)-[:IN_CONTEXT]->(c:Context) | c.name] AS projects
ORDER BY e.start
LIMIT $limit
"""


def write_events(driver: Driver, events: list[CalEvent], self_emails: list[str],
                 window: tuple[datetime, datetime] | None = None) -> None:
    rows = [e.to_graph(self_emails) for e in events]
    ids = [e.id for e in events]
    driver.execute_query(WRITE_EVENTS, events=rows, database_=database())
    if window:
        driver.execute_query(PRUNE_EVENTS, start=window[0], end=window[1], ids=ids, database_=database())
    driver.execute_query(LINK_THREADS, ids=ids, database_=database())


def summary(driver: Driver) -> dict:
    return driver.execute_query(SUMMARY, database_=database())[0][0].data()


def upcoming(driver: Driver, days: int = 7, limit: int = 20) -> list[dict]:
    return [r.data() for r in driver.execute_query(UPCOMING, days=days, limit=limit, database_=database())[0]]
