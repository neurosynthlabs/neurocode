-- 0008 · chats
--
-- A chat is a conversation that can act: you ask, a model answers or reaches for a tool, the tool
-- runs here and what it found goes back into the conversation. Every turn is a row — yours, the
-- model's, and each tool call with its result — so a conversation survives a reload, a restart and a
-- crash. Nothing about it lives only in a process's memory; losing work is what this product exists
-- to stop. (The table is `chats`, not `sessions`: a session here is a sign-in.)

CREATE TABLE chats (
  id         TEXT NOT NULL PRIMARY KEY,
  ref        TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  status     TEXT NOT NULL,              -- idle | thinking
  started    REAL NOT NULL,
  doc        TEXT NOT NULL
) STRICT;

CREATE INDEX chats_project ON chats(project_id, started DESC);
CREATE INDEX chats_started ON chats(started DESC);

CREATE TABLE chat_messages (
  id      INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  at      TEXT NOT NULL,
  role    TEXT NOT NULL,                 -- you | assistant | tool | note
  doc     TEXT NOT NULL
) STRICT;

CREATE INDEX chat_messages_of ON chat_messages(chat_id, id);
