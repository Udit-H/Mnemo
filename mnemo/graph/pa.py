"""Cypher behind the PA agent's tools. Every tool result in the UI shows the query that produced it."""

# Rank inbox threads by graph signals: how much you care about the people (CORRESPONDS_WITH.importance), whether
# it needs a reply, urgency, what you owe in it, and whether it links to your projects/goals from chat memory.
TRIAGE = """
MATCH (th:Thread)
WHERE th.in_inbox AND NOT coalesce(th.is_bulk, false) AND coalesce(th.category, '') <> 'automated'
  AND th.last_at > datetime() - duration({days: $days})
  AND NOT coalesce(th.dismissed, false)
OPTIONAL MATCH (th)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
OPTIONAL MATCH (:Me)-[c:CORRESPONDS_WITH]->(p)
WITH th, max(coalesce(c.importance, 0.0)) AS who, collect(DISTINCT coalesce(p.name, p.email))[..3] AS people,
     max(c.reply_rate) AS reply_rate
OPTIONAL MATCH (th)-[:HAS_COMMITMENT]->(cm:Commitment {owner: 'me', status: 'open'})
WITH th, who, people, reply_rate, collect(cm) AS mine
OPTIONAL MATCH (th)-[:IN_CONTEXT|RELATES_TO]->(m)
WITH th, who, people, reply_rate, mine, collect(DISTINCT coalesce(m.name, m.text)) AS linked
WITH th, who, people, reply_rate, linked,
     [x IN mine | {text: x.text, due: toString(x.due), overdue: x.due < date()}] AS you_owe,
     0.40 * who
     + CASE WHEN th.needs_reply THEN 0.25 ELSE 0.0 END
     + CASE th.urgency WHEN 'high' THEN 0.15 WHEN 'low' THEN -0.10 ELSE 0.0 END
     + CASE WHEN size(mine) > 0 THEN 0.10 ELSE 0.0 END
     + CASE WHEN size(linked) > 0 THEN 0.10 ELSE 0.0 END
     + CASE WHEN th.unread THEN 0.05 ELSE 0.0 END
     + coalesce(th.feedback_boost, 0.0) AS score
RETURN th.id AS thread_id, th.subject AS subject, th.summary AS summary, toString(date(th.last_at)) AS date,
       people, round(score, 2) AS score, round(who, 2) AS sender_importance, round(reply_rate, 2) AS your_reply_rate,
       th.needs_reply AS needs_reply, th.urgency AS urgency, th.category AS category, you_owe, linked AS linked_to
ORDER BY score DESC
LIMIT $limit
"""

PERSON = """
MATCH (p:Person)
WHERE NOT p:Self AND (toLower(p.email) CONTAINS toLower($q) OR toLower(coalesce(p.name, '')) CONTAINS toLower($q))
OPTIONAL MATCH (:Me)-[c:CORRESPONDS_WITH]->(p)
WITH p, c ORDER BY coalesce(c.importance, 0) DESC LIMIT 1
OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Org)
OPTIONAL MATCH (th:Thread)-[:HAS_EMAIL]->(:Email)-[:FROM|TO|CC]->(p)
WITH p, c, o, th ORDER BY th.last_at DESC
WITH p, c, o, collect(DISTINCT th)[..6] AS threads
RETURN p.name AS name, p.email AS email, o.domain AS org,
       CASE WHEN c IS NULL THEN null ELSE c {.received, .sent, .reply_rate, .avg_reply_hours, .importance,
                                             last: toString(date(c.last))} END AS relationship,
       [t IN threads | {thread_id: t.id, subject: t.subject, summary: t.summary, date: toString(date(t.last_at)),
                        needs_reply: t.needs_reply}] AS recent_threads,
       [(t2:Thread)-[:HAS_COMMITMENT]->(cm:Commitment {status: 'open'}) WHERE t2 IN threads |
          {commitment: cm.text, owner: cm.owner, due: toString(cm.due)}] AS open_commitments,
       [(pref:Preference)-[:APPLIES_TO]->(x) WHERE x = p OR x = o | pref.text] AS how_you_write_to_them,
       [(t3:Thread)-[:IN_CONTEXT]->(ctx:Context) WHERE t3 IN threads | ctx.name] AS related_projects
"""

COMMITMENTS = """
MATCH (th:Thread)-[:HAS_COMMITMENT]->(c:Commitment {status: 'open'})
WHERE ($owner = 'any' OR c.owner = $owner)
  AND (c.due IS NULL OR c.due >= date() - duration({days: $overdue_days}))
OPTIONAL MATCH (th)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
WITH th, c, collect(DISTINCT coalesce(p.name, p.email))[0] AS other_party
RETURN c.text AS commitment, c.owner AS owner, toString(c.due) AS due,
       CASE WHEN c.due IS NULL THEN null ELSE duration.inDays(date(), c.due).days END AS days_left,
       other_party, th.subject AS thread, th.id AS thread_id
ORDER BY c.due IS NULL, c.due
LIMIT $limit
"""

THREAD_SEARCH = """
CALL db.index.fulltext.queryNodes('thread_text', $q) YIELD node, score
OPTIONAL MATCH (node)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
RETURN node.id AS thread_id, node.subject AS subject, node.summary AS summary,
       toString(date(node.last_at)) AS date, collect(DISTINCT coalesce(p.name, p.email))[..3] AS people,
       round(score, 2) AS score
ORDER BY score DESC
LIMIT 6
"""

THREAD_GRAPH = """
MATCH (th:Thread {id: $id})
RETURN th.subject AS subject, th.summary AS summary, th.category AS category, th.needs_reply AS needs_reply,
       [(th)-[:HAS_EMAIL]->(:Email)-[:FROM|TO|CC]->(p:Person) WHERE NOT p:Self | coalesce(p.name, p.email)] AS people,
       [(th)-[:HAS_COMMITMENT]->(c) | {commitment: c.text, owner: c.owner, due: toString(c.due), status: c.status}]
         AS commitments,
       [(th)-[:IN_CONTEXT|RELATES_TO]->(m) | coalesce(m.name, m.text)] AS linked_to,
       [(th)-[:ABOUT]->(t:Topic) | t.name] AS topics
"""

# What the agent needs to write a reply in the user's voice for this person.
REPLY_CONTEXT = """
MATCH (th:Thread {id: $id})
OPTIONAL MATCH (th)-[:HAS_EMAIL]->(:Email)-[:FROM]->(p:Person) WHERE NOT p:Self
WITH th, p LIMIT 1
OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Org)
OPTIONAL MATCH (:Me)-[c:CORRESPONDS_WITH]->(p)
RETURN p.email AS email, p.name AS name, o.domain AS org,
       [(pref:Preference)-[:APPLIES_TO]->(x) WHERE x = p OR x = o | pref.text] AS scoped_preferences,
       [(:Me)-[r:PREFERS]->(g:Preference) WHERE r.until IS NULL AND NOT (g)-[:APPLIES_TO|APPLIES_IN]->() | g.text]
         AS global_preferences,
       [(e:Email)-[:FROM]->(:Self) WHERE (e)-[:TO|CC]->(p) | e.snippet][..3] AS your_past_emails_to_them,
       c.reply_rate AS reply_rate
"""

SCHEMA_HINT = """Nodes: Me, Person{email,name,is_bulk}, Self (the user's own Person), Org{domain},
Thread{id,subject,summary,category,needs_reply,urgency,last_at,unread,in_inbox}, Email{id,at,subject,snippet},
Commitment{text,owner:'me'|'them',due:date,status}, Context{name} (projects), Goal{text}, Topic{name},
Preference{text}, Conversation{title,summary,source} (past AI chats), Task{text,status}.
Rels: (Me)-[:IS]->(Self), (Me)-[:CORRESPONDS_WITH{received,sent,reply_rate,avg_reply_hours,importance,last}]->(Person),
(Person)-[:WORKS_AT]->(Org), (Thread)-[:HAS_EMAIL]->(Email), (Email)-[:FROM|TO|CC]->(Person),
(Thread)-[:HAS_COMMITMENT]->(Commitment)-[:OWED_BY]->(Person|Me), (Thread)-[:ABOUT]->(Topic),
(Thread)-[:IN_CONTEXT]->(Context), (Thread)-[:RELATES_TO]->(Goal), (Me)-[:HAS_GOAL]->(Goal),
(Me)-[:PREFERS{since,until}]->(Preference), (Conversation)-[:ABOUT]->(Topic), (Conversation)-[:IN_CONTEXT]->(Context),
(Task)-[:FROM]->(Conversation), (Task)-[:SERVES]->(Goal)."""

# Everything around one thread, for the thread panel and its graph: people (+ how the user treats them), orgs,
# commitments and who owes them, topics, linked projects/goals, and past AI chats in the same project.
THREAD_NEIGHBORHOOD = """
MATCH (th:Thread {id: $id})
RETURN th.id AS id, th.subject AS subject, th.summary AS summary, th.category AS category,
       th.needs_reply AS needs_reply, th.urgency AS urgency, toString(date(th.last_at)) AS date,
       [(th)-[:HAS_EMAIL]->(:Email)-[:FROM|TO|CC]->(p:Person) WHERE NOT p:Self |
          p {.email, .name, org: [(p)-[:WORKS_AT]->(o) | o.domain][0],
             importance: [(:Me)-[c:CORRESPONDS_WITH]->(p) | round(c.importance, 2)][0],
             reply_rate: [(:Me)-[c:CORRESPONDS_WITH]->(p) | c.reply_rate][0],
             avg_reply_hours: [(:Me)-[c:CORRESPONDS_WITH]->(p) | round(c.avg_reply_hours, 1)][0]}] AS people,
       [(th)-[:HAS_COMMITMENT]->(c:Commitment) |
          c {.id, .text, .owner, .status, due: toString(c.due),
             by: [(c)-[:OWED_BY]->(o) | coalesce(o.email, 'me')][0]}] AS commitments,
       [(th)-[:ABOUT]->(t:Topic) | t.name] AS topics,
       [(th)-[:IN_CONTEXT]->(x:Context) | x.name] AS projects,
       [(th)-[:RELATES_TO]->(g:Goal) | g.text] AS goals,
       [(th)-[:IN_CONTEXT]->(:Context)<-[:IN_CONTEXT]-(conv:Conversation) | conv.title][..3] AS related_chats
"""
