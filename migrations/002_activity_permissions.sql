CREATE TABLE activity_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 occurred_at TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 actor_name TEXT NOT NULL,
 action TEXT NOT NULL,
 entity_type TEXT NOT NULL,
 entity_id TEXT NOT NULL,
 entity_name TEXT NOT NULL,
 board_id TEXT,
 board_name TEXT,
 task_id TEXT,
 task_name TEXT,
 previous_values TEXT NOT NULL,
 new_values TEXT NOT NULL
);
CREATE INDEX activity_actor ON activity_log(actor_id, id DESC);
CREATE INDEX activity_task ON activity_log(task_id, id DESC);
CREATE INDEX activity_action ON activity_log(action, id DESC);
CREATE INDEX activity_date ON activity_log(occurred_at);
