-- Records the AI features produce and the operator keeps.
CREATE TABLE brainstorms (
  id         TEXT PRIMARY KEY,
  project_id TEXT,
  created    REAL NOT NULL,
  doc        TEXT NOT NULL
);
