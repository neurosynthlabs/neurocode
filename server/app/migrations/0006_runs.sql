-- Agent runs: real work on real code, in a git worktree of its own. The run itself is a document (the
-- shape the screens read); its output is a log, kept relationally because there is a lot of it.

CREATE TABLE runs (
  id         TEXT PRIMARY KEY,
  ref        TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  status     TEXT NOT NULL,          -- queued | running | waiting | done | failed | cancelled
  started    REAL NOT NULL,
  doc        TEXT NOT NULL
) STRICT;
CREATE INDEX runs_project ON runs(project_id, started);
CREATE INDEX runs_status  ON runs(status);

CREATE TABLE run_logs (
  id     INTEGER PRIMARY KEY,
  run_id TEXT    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  at     TEXT    NOT NULL,
  step   INTEGER,
  level  TEXT    NOT NULL,           -- info | ok | warn | err | tool
  line   TEXT    NOT NULL
) STRICT;
CREATE INDEX run_logs_run ON run_logs(run_id, id);
