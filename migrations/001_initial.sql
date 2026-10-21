CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('Administrator','Manager','Member','Viewer')),
 permissions TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 must_change_password INTEGER NOT NULL DEFAULT 1 CHECK(must_change_password IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_expiry ON sessions(expires_at);
CREATE TABLE login_attempts (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_start INTEGER NOT NULL);
CREATE TABLE boards (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE columns (
 id TEXT PRIMARY KEY, board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
 name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('todo','doing','done')),
 position INTEGER NOT NULL, UNIQUE(id, board_id), UNIQUE(board_id, name)
);
CREATE INDEX columns_order ON columns(board_id, position);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
 column_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 priority TEXT NOT NULL CHECK(priority IN ('normal','high','low')), position INTEGER NOT NULL,
 created_by TEXT REFERENCES users(id) ON DELETE SET NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(column_id, board_id) REFERENCES columns(id, board_id)
);
CREATE INDEX tasks_order ON tasks(board_id, column_id, position);
CREATE TABLE task_assignees (
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY(task_id,user_id)
);
CREATE TABLE attachments (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, format TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
 uploaded_by TEXT REFERENCES users(id) ON DELETE SET NULL, uploaded_at TEXT NOT NULL, content BLOB NOT NULL
);
CREATE INDEX attachments_task ON attachments(task_id);
