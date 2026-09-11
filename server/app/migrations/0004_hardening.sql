-- Indexes for the lists the API serves most, an append-only audit log, and the usage ledger.

CREATE INDEX IF NOT EXISTS tasks_project    ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS memory_scope     ON memory(archived, project_id, category);
CREATE INDEX IF NOT EXISTS plans_created    ON plans(created);
CREATE INDEX IF NOT EXISTS plans_project    ON plans(project_id);
CREATE INDEX IF NOT EXISTS conflicts_status ON conflicts(status);
CREATE INDEX IF NOT EXISTS brainstorms_made ON brainstorms(created);
CREATE INDEX IF NOT EXISTS sessions_expiry  ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS audit_user       ON audit_log(user_id, seq);

-- The audit log is append-only: the database itself refuses to change or remove an entry.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

-- Every attempt to use a model, and every answer the offline rules gave: the source of truth for usage.
CREATE TABLE ai_calls (
  id         INTEGER PRIMARY KEY,
  at         TEXT    NOT NULL,
  feature    TEXT    NOT NULL,                -- compile | ask | brainstorm | extract | test
  provider   TEXT    NOT NULL CHECK (provider IN ('deepseek', 'ollama', 'rules')),
  model      TEXT    NOT NULL,
  ok         INTEGER NOT NULL CHECK (ok IN (0, 1)),
  ms         INTEGER NOT NULL,
  tokens_in  INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  user_id    TEXT    REFERENCES users(id) ON DELETE SET NULL,
  project_id TEXT,
  error      TEXT    NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX ai_calls_at      ON ai_calls(at);
CREATE INDEX ai_calls_feature ON ai_calls(feature, at);
