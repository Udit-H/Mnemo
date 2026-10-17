// Constraints, indexes and seed nodes. Idempotent: safe to re-run.
// Applied by mnemo.graph.db.apply_schema(). __EMBEDDING_DIM__ is replaced from .env.

// --- Uniqueness ---
CREATE CONSTRAINT me_id IF NOT EXISTS FOR (n:Me) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT domain_name IF NOT EXISTS FOR (n:Domain) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT context_id IF NOT EXISTS FOR (n:Context) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT conversation_id IF NOT EXISTS FOR (n:Conversation) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT message_id IF NOT EXISTS FOR (n:Message) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT topic_name IF NOT EXISTS FOR (n:Topic) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (n:Entity) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT goal_id IF NOT EXISTS FOR (n:Goal) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT task_id IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT outcome_id IF NOT EXISTS FOR (n:Outcome) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT preference_id IF NOT EXISTS FOR (n:Preference) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT correction_id IF NOT EXISTS FOR (n:Correction) REQUIRE n.id IS UNIQUE;

// --- Fulltext (entity linking for recall) ---
CREATE FULLTEXT INDEX memory_names IF NOT EXISTS
FOR (n:Entity|Topic|Context|Goal|Preference) ON EACH [n.name, n.text];

// --- Vector (semantic recall) ---
CREATE VECTOR INDEX topic_embedding IF NOT EXISTS
FOR (n:Topic) ON n.embedding
OPTIONS {indexConfig: {`vector.dimensions`: __EMBEDDING_DIM__, `vector.similarity_function`: 'cosine'}};

CREATE VECTOR INDEX message_embedding IF NOT EXISTS
FOR (n:Message) ON n.embedding
OPTIONS {indexConfig: {`vector.dimensions`: __EMBEDDING_DIM__, `vector.similarity_function`: 'cosine'}};

// --- Other lookups ---
CREATE INDEX conversation_started IF NOT EXISTS FOR (n:Conversation) ON (n.started_at);

// --- Seed ---
MERGE (:Me {id: 'me'});
UNWIND ['coding', 'learning', 'planning', 'health', 'finance', 'career', 'personal'] AS d
MERGE (:Domain {name: d});

// --- v2: email ---
CREATE CONSTRAINT person_email IF NOT EXISTS FOR (n:Person) REQUIRE n.email IS UNIQUE;
CREATE CONSTRAINT org_domain IF NOT EXISTS FOR (n:Org) REQUIRE n.domain IS UNIQUE;
CREATE CONSTRAINT thread_id IF NOT EXISTS FOR (n:Thread) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT email_id IF NOT EXISTS FOR (n:Email) REQUIRE n.id IS UNIQUE;
CREATE INDEX thread_last_at IF NOT EXISTS FOR (n:Thread) ON (n.last_at);
CREATE CONSTRAINT commitment_id IF NOT EXISTS FOR (n:Commitment) REQUIRE n.id IS UNIQUE;
CREATE FULLTEXT INDEX thread_text IF NOT EXISTS FOR (n:Thread) ON EACH [n.subject, n.summary];

// --- v2: calendar ---
CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE;
CREATE INDEX event_start IF NOT EXISTS FOR (n:Event) ON (n.start);
