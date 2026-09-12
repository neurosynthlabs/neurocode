-- 0007 · model lanes
--
-- The usage ledger was written when there were three providers and a CHECK constraint to prove it.
-- A lane is a provider and a model together (groq/llama-3.3-70b, ollama/qwen2.5-coder), several
-- lanes answer at once, and the router reads this table to know what a lane has already spent in the
-- last minute and today. So the constraint has to go and the lane has to be recorded. SQLite cannot
-- drop a CHECK, so the table is rebuilt in place; every old row keeps its history, with its lane
-- filled in from the provider it used to name.

CREATE TABLE ai_calls_next (
  id         INTEGER PRIMARY KEY,
  at         TEXT    NOT NULL,
  feature    TEXT    NOT NULL,                -- compile | ask | brainstorm | extract | agent | review | test
  lane       TEXT    NOT NULL DEFAULT '',     -- groq | cerebras | gemini | deepseek | ollama | rules | …
  provider   TEXT    NOT NULL,
  model      TEXT    NOT NULL,
  ok         INTEGER NOT NULL CHECK (ok IN (0, 1)),
  ms         INTEGER NOT NULL,
  tokens_in  INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  user_id    TEXT    REFERENCES users(id) ON DELETE SET NULL,
  project_id TEXT,
  error      TEXT    NOT NULL DEFAULT ''
) STRICT;

INSERT INTO ai_calls_next (id, at, feature, lane, provider, model, ok, ms, tokens_in, tokens_out,
                           user_id, project_id, error)
SELECT id, at, feature, provider, provider, model, ok, ms, tokens_in, tokens_out, user_id, project_id, error
FROM ai_calls;

DROP TABLE ai_calls;
ALTER TABLE ai_calls_next RENAME TO ai_calls;

CREATE INDEX ai_calls_at      ON ai_calls(at);
CREATE INDEX ai_calls_feature ON ai_calls(feature, at);
CREATE INDEX ai_calls_lane    ON ai_calls(lane, at);
