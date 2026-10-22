CREATE TABLE task_numbers (
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL UNIQUE
);
INSERT INTO task_numbers(task_id) SELECT id FROM tasks ORDER BY created_at,id;
ALTER TABLE tasks ADD COLUMN task_number INTEGER;
ALTER TABLE tasks ADD COLUMN order_number TEXT NOT NULL DEFAULT '';
UPDATE tasks SET task_number=(SELECT number FROM task_numbers WHERE task_id=tasks.id);
CREATE UNIQUE INDEX tasks_number ON tasks(task_number);
CREATE TRIGGER tasks_assign_number AFTER INSERT ON tasks
BEGIN
 INSERT INTO task_numbers(task_id) VALUES(NEW.id);
 UPDATE tasks SET task_number=(SELECT number FROM task_numbers WHERE task_id=NEW.id) WHERE id=NEW.id;
END;
CREATE TRIGGER tasks_number_immutable BEFORE UPDATE OF task_number ON tasks
WHEN OLD.task_number IS NOT NULL AND NEW.task_number IS NOT OLD.task_number
BEGIN
 SELECT RAISE(ABORT,'Task numbers are immutable');
END;
CREATE TRIGGER task_numbers_no_delete BEFORE DELETE ON task_numbers
BEGIN
 SELECT RAISE(ABORT,'Task numbers cannot be reused');
END;
CREATE TRIGGER task_numbers_no_update BEFORE UPDATE ON task_numbers
BEGIN
 SELECT RAISE(ABORT,'Task numbers are immutable');
END;
ALTER TABLE activity_log ADD COLUMN task_number INTEGER;
UPDATE activity_log SET task_number=(SELECT task_number FROM tasks WHERE tasks.id=activity_log.task_id);
