-- Domain records, kept as JSON documents shaped like the frontend's types. The fields the API filters
-- on or mutates are lifted into columns and kept in step with the document. IF NOT EXISTS, because
-- databases made before migrations existed already hold these tables.
CREATE TABLE IF NOT EXISTS projects  (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents    (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks     (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT, status TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, status TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory    (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT, category TEXT,
                                      pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, doc TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(ref, title, body, reason, tags, content='');
CREATE TABLE IF NOT EXISTS plans     (id TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL, project_id TEXT,
                                      created REAL NOT NULL DEFAULT 0, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open', doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mcp       (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prefs     (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity  (seq INTEGER PRIMARY KEY AUTOINCREMENT, position REAL NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_position ON activity(position);
