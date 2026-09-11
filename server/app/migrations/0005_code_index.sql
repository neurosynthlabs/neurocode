-- The code index. A project's rows are replaced in one transaction on every (re)index, so a reader never
-- sees half an index, and they go when the project goes.

CREATE TABLE code_files (
  id         INTEGER PRIMARY KEY,
  project_id TEXT    NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path       TEXT    NOT NULL,                -- relative to the project root, forward slashes
  lang       TEXT    NOT NULL,
  module     TEXT    NOT NULL,
  lines      INTEGER NOT NULL,
  bytes      INTEGER NOT NULL,
  sha1       TEXT    NOT NULL,
  complexity INTEGER NOT NULL DEFAULT 0,      -- decision points: if, for, while, case, catch, &&, ||
  churn      INTEGER NOT NULL DEFAULT 0,      -- commits touching it in the last 90 days
  changed_at TEXT,                            -- its last commit, when the project is a git repository
  UNIQUE (project_id, path)
) STRICT;
CREATE INDEX code_files_module ON code_files(project_id, module);

CREATE TABLE code_symbols (
  id         INTEGER PRIMARY KEY,
  project_id TEXT    NOT NULL,
  file_id    INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  name       TEXT    NOT NULL,
  kind       TEXT    NOT NULL,
  line       INTEGER NOT NULL,
  exported   INTEGER NOT NULL DEFAULT 0 CHECK (exported IN (0, 1))
) STRICT;
CREATE INDEX code_symbols_file ON code_symbols(file_id);
CREATE INDEX code_symbols_name ON code_symbols(project_id, name COLLATE NOCASE);

-- from_file depends on to_file (imports, uses) or on a database object (reads, writes, calls).
-- to_file is null when the target lives outside the repository, such as a package.
CREATE TABLE code_edges (
  id         INTEGER PRIMARY KEY,
  project_id TEXT    NOT NULL,
  from_file  INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  to_file    INTEGER REFERENCES code_files(id) ON DELETE CASCADE,
  target     TEXT    NOT NULL,
  kind       TEXT    NOT NULL CHECK (kind IN ('imports', 'uses', 'reads', 'writes', 'calls'))
) STRICT;
CREATE INDEX code_edges_from   ON code_edges(from_file);
CREATE INDEX code_edges_to     ON code_edges(to_file);
CREATE INDEX code_edges_target ON code_edges(project_id, target COLLATE NOCASE);

CREATE TABLE code_index_runs (
  project_id  TEXT    PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  root        TEXT    NOT NULL,
  finished_at TEXT    NOT NULL,
  ms          INTEGER NOT NULL,
  files       INTEGER NOT NULL,
  symbols     INTEGER NOT NULL,
  edges       INTEGER NOT NULL,
  unresolved  INTEGER NOT NULL,
  parsers     TEXT    NOT NULL DEFAULT '{}'   -- language → the parser that read it, as JSON
) STRICT;

-- Search over file paths and symbol names. `words` splits camelCase and snake_case, so "tax" finds TaxService.
CREATE VIRTUAL TABLE code_fts USING fts5(name, words, path, kind, project_id UNINDEXED, file_id UNINDEXED, line UNINDEXED);
