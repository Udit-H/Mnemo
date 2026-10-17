"""G5: user actions → graph. Every action is logged as (:Me)-[:GAVE]->(:Feedback)-[:ON]->(target) so the UI can
show what Mnemo learned, and each one changes something the PA uses next time (ranking, drafts, commitments)."""

from neo4j import Driver

from mnemo.extract.schema import stable_id
from mnemo.graph.db import database

MAX_BOOST = 0.4

# Dismiss / prioritise: nudge how much the user cares about the thread's senders. Importance is updated in place,
# and feedback_boost survives `gmail_sync --stats-only` (it's added back in the importance formula).
ADJUST_SENDERS = """
MATCH (th:Thread {id: $id})
SET th.dismissed = $dismiss, th.feedback_boost = coalesce(th.feedback_boost, 0.0) + $delta
WITH th
MATCH (me:Me {id: 'me'})
MERGE (me)-[:GAVE]->(f:Feedback {id: randomUUID()})
SET f.kind = $kind, f.at = datetime()
MERGE (f)-[:ON]->(th)
WITH th, f
MATCH (th)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
WITH DISTINCT th, f, p
MATCH (:Me)-[c:CORRESPONDS_WITH]->(p)
WITH f, p, c, coalesce(c.feedback_boost, 0.0) AS old
WITH f, p, c, old,
     CASE WHEN old + $delta > $max THEN $max WHEN old + $delta < -$max THEN -$max ELSE old + $delta END AS new
SET c.feedback_boost = new, c.importance = c.importance + (new - old)
MERGE (f)-[:ABOUT_PERSON]->(p)
RETURN coalesce(p.name, p.email) AS person, round(c.importance, 2) AS importance
"""

SET_COMMITMENT = """
MATCH (c:Commitment {id: $id})
SET c.status = $status, c.done_at = CASE WHEN $status = 'done' THEN datetime() ELSE null END
WITH c
MATCH (me:Me {id: 'me'})
MERGE (me)-[:GAVE]->(f:Feedback {id: randomUUID()})
SET f.kind = 'commitment_' + $status, f.at = datetime()
MERGE (f)-[:ON]->(c)
"""

# A preference learned from a draft edit, scoped to the person, their org, or everyone.
LEARN_PREFERENCE = """
MATCH (me:Me {id: 'me'}), (th:Thread {id: $thread_id})
OPTIONAL MATCH (p:Person {email: $email})
OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Org)
MERGE (pref:Preference {id: $pref_id})
ON CREATE SET pref.text = $text, pref.source = 'draft_edit'
MERGE (me)-[r:PREFERS]->(pref)
ON CREATE SET r.since = datetime()
SET r.mentions = coalesce(r.mentions, 0) + 1
FOREACH (_ IN CASE WHEN $scope = 'person' AND p IS NOT NULL THEN [1] ELSE [] END | MERGE (pref)-[:APPLIES_TO]->(p))
FOREACH (_ IN CASE WHEN $scope = 'org' AND o IS NOT NULL THEN [1] ELSE [] END | MERGE (pref)-[:APPLIES_TO]->(o))
MERGE (me)-[:GAVE]->(f:Feedback {id: $feedback_id})
ON CREATE SET f.kind = 'edited_draft', f.at = datetime(), f.before = $before, f.after = $after
MERGE (f)-[:ON]->(th)
MERGE (f)-[:TAUGHT]->(pref)
"""

FEEDBACK_LOG = """
MATCH (:Me)-[:GAVE]->(f:Feedback)
OPTIONAL MATCH (f)-[:ON]->(t)
OPTIONAL MATCH (f)-[:TAUGHT]->(pref:Preference)
OPTIONAL MATCH (f)-[:ABOUT_PERSON]->(p:Person)
RETURN toString(f.at) AS at, f.kind AS action, coalesce(t.subject, t.text) AS on,
       collect(DISTINCT pref.text) AS learned, collect(DISTINCT coalesce(p.name, p.email)) AS people
ORDER BY at DESC
LIMIT 30
"""

SCOPED_PREFERENCES = """
MATCH (:Me)-[r:PREFERS]->(pref:Preference)-[:APPLIES_TO]->(x)
WHERE r.until IS NULL
RETURN pref.text AS preference, coalesce(x.name, x.email, x.domain) AS applies_to, labels(x)[0] AS kind,
       toString(date(r.since)) AS since, r.mentions AS times_seen
ORDER BY since DESC
"""


def _rows(driver: Driver, query: str, **params) -> list[dict]:
    return [r.data() for r in driver.execute_query(query, params, database_=database())[0]]


def dismiss_thread(driver: Driver, thread_id: str) -> list[dict]:
    return _rows(driver, ADJUST_SENDERS, id=thread_id, dismiss=True, delta=-0.15, kind="dismissed", max=MAX_BOOST)


def prioritise_thread(driver: Driver, thread_id: str) -> list[dict]:
    return _rows(driver, ADJUST_SENDERS, id=thread_id, dismiss=False, delta=0.15, kind="prioritised",
                 max=MAX_BOOST)


def set_commitment(driver: Driver, commitment_id: str, done: bool) -> None:
    _rows(driver, SET_COMMITMENT, id=commitment_id, status="done" if done else "open")


def learn_preferences(driver: Driver, thread_id: str, email: str, prefs: list[dict], before: str,
                      after: str) -> None:
    feedback_id = stable_id("fb", thread_id, after)
    for p in prefs:
        target = email if p["scope"] == "person" else email.rsplit("@", 1)[-1] if p["scope"] == "org" else "all"
        _rows(driver, LEARN_PREFERENCE, thread_id=thread_id, email=email, scope=p["scope"], text=p["text"],
              pref_id=stable_id("pref", p["text"], target), feedback_id=feedback_id, before=before[:2000],
              after=after[:2000])


def feedback_log(driver: Driver) -> list[dict]:
    return _rows(driver, FEEDBACK_LOG)


def scoped_preferences(driver: Driver) -> list[dict]:
    return _rows(driver, SCOPED_PREFERENCES)


RESET_FEEDBACK = [
    "MATCH (f:Feedback) OPTIONAL MATCH (f)-[:TAUGHT]->(p:Preference {source: 'draft_edit'}) DETACH DELETE f, p",
    "MATCH (th:Thread) WHERE th.dismissed IS NOT NULL OR th.feedback_boost IS NOT NULL "
    "REMOVE th.dismissed, th.feedback_boost",
    "MATCH (:Me)-[c:CORRESPONDS_WITH]->() WHERE c.feedback_boost IS NOT NULL REMOVE c.feedback_boost",
]


def reset_feedback(driver: Driver) -> None:
    """Forget all UI feedback (e.g. after rehearsing the demo). Call email.recompute() after to restore importance.
    Commitment checkboxes are left alone: they reflect real-world state."""
    for q in RESET_FEEDBACK:
        driver.execute_query(q, database_=database())
