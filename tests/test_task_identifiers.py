import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import test_app
from app import connect, create_app


class TaskIdentifiers(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    task = test_app.KanbanTests.task
    board = test_app.KanbanTests.board
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    def test_numbers_and_order_survive_edit_move_delete_and_restart(self):
        first, second = self.task(), self.task()
        self.assertEqual([t['task_number'] for t in self.board()['tasks']], [1, 2])
        result = self.request('PATCH', '/api/tasks/' + second, {
            'version': self.board()['version'], 'title': 'Numbered task', 'order_number': '000AB-17', 'task_number': 100})
        self.assertEqual(result.status_code, 200, result.json)
        saved = next(t for t in self.board()['tasks'] if t['id'] == second)
        self.assertEqual((saved['task_number'], saved['order_number']), (2, '000AB-17'))
        self.request('POST', '/api/tasks/' + second + '/move', {
            'version': self.board()['version'], 'column_id': self.board()['columns'][1]['id']})
        self.assertEqual(self.request('DELETE', '/api/tasks/' + first, {'version': self.board()['version']}).status_code, 200)
        third = self.task()
        create_app(self.config)
        saved = {t['id']: t for t in self.board()['tasks']}
        self.assertEqual(saved[third]['task_number'], 3)
        self.assertEqual(saved[second]['task_number'], 2)
        history = self.client.get('/api/tasks/' + second + '/history').json['entries']
        self.assertTrue(history)
        self.assertTrue(all(entry['task_number'] == 2 for entry in history))
        self.assertTrue(any(e['action'] == 'task.updated' and e['new']['order_number'] == '000AB-17' for e in history))

    def test_migration_orders_timestamps_and_ids_and_preserves_relationships(self):
        ids = [self.task() for _ in range(3)]
        with closing(connect(self.path)) as db:
            test_app.remove_task_identifiers(db)
            db.execute('UPDATE tasks SET created_at=?', ('2025-01-02T00:00:00Z',))
            db.execute('UPDATE tasks SET created_at=? WHERE id=?', ('2025-01-01T00:00:00Z', ids[2]))
            expected = [r[0] for r in db.execute('SELECT id FROM tasks ORDER BY created_at,id')]
            original = [tuple(r) for r in db.execute('SELECT id,title,column_id,created_at FROM tasks ORDER BY id')]
            original_assignments = [tuple(r) for r in db.execute('SELECT task_id,user_id FROM task_assignees ORDER BY task_id,user_id')]
            db.execute('PRAGMA user_version=6')
        create_app(self.config)
        create_app(self.config)
        with closing(connect(self.path)) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT id FROM tasks ORDER BY task_number')], expected)
            self.assertEqual([tuple(r) for r in db.execute('SELECT id,title,column_id,created_at FROM tasks ORDER BY id')], original)
            self.assertEqual([tuple(r) for r in db.execute('SELECT task_id,user_id FROM task_assignees ORDER BY task_id,user_id')], original_assignments)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        new = self.task()
        self.assertEqual(next(t for t in self.board()['tasks'] if t['id'] == new)['task_number'], 4)

    def test_concurrent_creation_is_global_and_numbers_are_database_immutable(self):
        first = self.board()
        result = self.request('POST', '/api/boards', {'name': 'Another board'})
        self.assertEqual(result.status_code, 201, result.json)
        with closing(connect(self.path)) as db:
            columns = [tuple(r) for r in db.execute('SELECT id,board_id FROM columns')]

        def insert(index):
            with closing(connect(self.path)) as db:
                db.execute('BEGIN IMMEDIATE')
                column, board = columns[index % len(columns)]
                db.execute('INSERT INTO tasks(id,board_id,column_id,title,priority,position,created_at,updated_at) '
                           'VALUES(?,?,?,?,?,?,?,?)', (str(index), board, column, 'Concurrent', 'normal', index, '2026-01-01', '2026-01-01'))
                number = db.execute('SELECT task_number FROM tasks WHERE id=?', (str(index),)).fetchone()[0]
                db.commit()
                return number

        with ThreadPoolExecutor(max_workers=6) as pool:
            numbers = list(pool.map(insert, range(18)))
        self.assertEqual(sorted(numbers), list(range(1, 19)))
        with closing(connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE tasks SET task_number=500 WHERE id=?', ('0',))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM task_numbers')

    def test_order_validation_and_permissions(self):
        task = self.task()
        for value in (123, None, 'a' * 101):
            response = self.request('PATCH', '/api/tasks/' + task, {'version': self.board()['version'], 'title': 'Task', 'order_number': value})
            self.assertEqual(response.status_code, 400)
        self.create_user(role='Viewer')
        client, csrf = self.login_user()
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task, {
            'version': self.board()['version'], 'title': 'Task', 'order_number': '123'}, client, csrf).status_code, 403)
