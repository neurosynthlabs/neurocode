-- 0009 · retrieval
--
-- A chunk is the smallest piece of this workspace worth retrieving on its own: a symbol with the
-- lines around it, a section of a document, a remembered fact. Each keeps the text itself, so an
-- answer can quote it and say where it came from, and — when a lane that makes embeddings is
-- configured — the vector for it.
--
-- Two searches over the same rows. Lexical (FTS5) always works, with no key and no model. Semantic
-- (cosine over the vectors) only sharpens it. They are fused by rank, so retrieval degrades to
-- something honest instead of disappearing when there is no model.

CREATE TABLE chunks (
  id         INTEGER PRIMARY KEY,
  project_id TEXT    NOT NULL,                 -- a project's id, or 'global' for workspace memory
  kind       TEXT    NOT NULL,                 -- code | doc | memory
  ref        TEXT    NOT NULL,                 -- path#symbol, path#section, or a fact's ref
  path       TEXT    NOT NULL DEFAULT '',
  title      TEXT    NOT NULL DEFAULT '',
  line       INTEGER NOT NULL DEFAULT 0,
  text       TEXT    NOT NULL,
  vector     BLOB,                             -- float32, normalised; null until a lane embeds it
  dim        INTEGER NOT NULL DEFAULT 0,
  model      TEXT    NOT NULL DEFAULT '',
  at         TEXT    NOT NULL
) STRICT;

CREATE UNIQUE INDEX chunks_ref     ON chunks(project_id, kind, ref);
CREATE INDEX        chunks_project ON chunks(project_id, kind);

CREATE VIRTUAL TABLE chunk_fts USING fts5(title, text, path, project_id UNINDEXED, chunk_id UNINDEXED);

CREATE TABLE retrieval_runs (
  project_id  TEXT    NOT NULL PRIMARY KEY,
  finished_at TEXT    NOT NULL,
  ms          INTEGER NOT NULL,
  chunks      INTEGER NOT NULL,
  embedded    INTEGER NOT NULL,
  model       TEXT    NOT NULL DEFAULT '',
  lane        TEXT    NOT NULL DEFAULT '',
  note        TEXT    NOT NULL DEFAULT ''
) STRICT;
