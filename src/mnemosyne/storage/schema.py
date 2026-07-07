"""Shared DDL for both tiers. Tier semantics come from which file the schema
lives in and which store interface fronts it, not from the schema itself."""

import sqlite3

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
  id              TEXT PRIMARY KEY,
  entity_type     TEXT NOT NULL,
  name            TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  summary         TEXT,
  attributes      TEXT,
  confidence      REAL NOT NULL DEFAULT 1.0,
  extends_id      TEXT,
  valid_from      TEXT NOT NULL,
  valid_to        TEXT,
  superseded_by   TEXT,
  provenance_id   TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_norm ON entities(normalized_name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

CREATE TABLE IF NOT EXISTS aliases (
  id               TEXT PRIMARY KEY,
  -- No FK: overlay aliases may reference canonical entities in the other tier
  -- (learned spellings for shared entities live in the private overlay).
  entity_id        TEXT NOT NULL,
  alias            TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  alias_type       TEXT NOT NULL DEFAULT 'name',
  confidence       REAL NOT NULL DEFAULT 1.0,
  provenance_id    TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  UNIQUE(entity_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS relationships (
  id               TEXT PRIMARY KEY,
  source_entity_id TEXT NOT NULL,
  target_entity_id TEXT NOT NULL,
  rel_type         TEXT NOT NULL,
  attributes       TEXT,
  confidence       REAL NOT NULL DEFAULT 1.0,
  valid_from       TEXT NOT NULL,
  valid_to         TEXT,
  superseded_by    TEXT,
  provenance_id    TEXT NOT NULL,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id, rel_type);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id, rel_type);

CREATE TABLE IF NOT EXISTS memories (
  id            TEXT PRIMARY KEY,
  content       TEXT NOT NULL,
  memory_kind   TEXT NOT NULL DEFAULT 'fact',
  confidence    REAL NOT NULL DEFAULT 1.0,
  valid_from    TEXT NOT NULL,
  valid_to      TEXT,
  superseded_by TEXT,
  provenance_id TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  role      TEXT DEFAULT 'about',
  PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE IF NOT EXISTS provenance (
  id                TEXT PRIMARY KEY,
  source_type       TEXT NOT NULL,
  assistant_id      TEXT,
  session_id        TEXT,
  stated_confidence REAL,
  derivation        TEXT,
  raw_context       TEXT,
  recorded_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assertions (
  id            TEXT PRIMARY KEY,
  seq           INTEGER,
  kind          TEXT NOT NULL,
  subject_type  TEXT NOT NULL,
  subject_id    TEXT NOT NULL,
  payload       TEXT NOT NULL,
  provenance_id TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assert_subject ON assertions(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS merge_proposals (
  id             TEXT PRIMARY KEY,
  candidate_name TEXT NOT NULL,
  entity_id      TEXT NOT NULL,
  score          REAL NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending',
  provenance_id  TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  resolved_at    TEXT
);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
USING fts5(content, content='memories', content_rowid='rowid');
"""


def init_schema(conn: sqlite3.Connection, tier: str, with_fts: bool = False) -> None:
    conn.executescript(DDL)
    if with_fts and fts5_available(conn):
        conn.executescript(FTS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('tier', ?)", (tier,))
    conn.commit()


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False
