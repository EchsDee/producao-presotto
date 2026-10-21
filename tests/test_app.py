import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from app import PH, connect, create_app
from backup import backup


class KanbanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'kanban.db')
        self.config = {'TESTING': True, 'DATABASE': self.path, 'MAX_FILE_BYTES': 4096}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        result = self.client.post('/api/login', json={'username': 'Monke', 'password': 'monkeman'})
        self.assertEqual(result.status_code, 200)
        self.csrf = result.json['csrf']
        self.admin = result.json['user']
        result = self.request('POST', '/api/password', {'current_password': 'monkeman', 'new_password': 'admin-password-123', 'confirm_password': 'admin-password-123'})
        self.assertEqual(result.status_code, 200)
        self.csrf = result.json['csrf']
        self.admin = result.json['user']
        self.board_id = self.client.get('/api/boards').json['boards'][0]['id']

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, data=None, client=None, csrf=None, **kwargs):
        return (client or self.client).open(path, method=method, json=data,
            headers={'X-CSRF-Token': csrf or self.csrf}, **kwargs)

    def board(self):
        result = self.client.get('/api/boards/' + self.board_id)
        self.assertEqual(result.status_code, 200)
        return result.json['board']

    def create_user(self, username='other', role='Member', overrides=None):
        result = self.request('POST', '/api/users', {'username': username, 'password': 'temporary-password',
            'role': role, 'overrides': overrides or {}})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json['user']

    def login_user(self, username='other'):
        client = self.app.test_client()
        login = client.post('/api/login', json={'username': username, 'password': 'temporary-password'})
        self.assertEqual(login.status_code, 200, login.json)
        result = self.request('POST', '/api/password',
            {'current_password': 'temporary-password', 'new_password': 'changed-password-123', 'confirm_password': 'changed-password-123'},
            client, login.json['csrf'])
        self.assertEqual(result.status_code, 200, result.json)
        return client, result.json['csrf']

    def task(self, client=None, csrf=None):
        board = self.board()
        result = self.request('POST', f'/api/boards/{self.board_id}/tasks', {
            'version': board['version'], 'column_id': board['columns'][0]['id'],
            'title': 'Task', 'description': 'Details', 'priority': 'high', 'assignees': []}, client, csrf)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json['task_id']

    def upload(self, task_id, filename, content, client=None, csrf=None, version=None):
        return (client or self.client).post(f'/api/tasks/{task_id}/attachments',
            data={'file': (io.BytesIO(content), filename)}, content_type='multipart/form-data',
            headers={'X-CSRF-Token': csrf or self.csrf,
                     'If-Match': str(version if version is not None else self.board()['version'])})

    def test_initial_password_gate_hash_and_initialization_marker(self):
        other = self.create_user()
        client = self.app.test_client()
        login = client.post('/api/login', json={'username': 'other', 'password': 'temporary-password'})
        self.assertTrue(login.json['user']['must_change_password'])
        self.assertEqual(client.get('/api/boards').status_code, 403)
        with closing(connect(self.path)) as db:
            stored = db.execute('SELECT password_hash FROM users WHERE id=?', (other['id'],)).fetchone()[0]
            self.assertTrue(stored.startswith('$argon2id$'))
            self.assertTrue(PH.verify(stored, 'temporary-password'))
            db.execute('DELETE FROM users')
        create_app(self.config)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)

    def test_login_logout_cookie_and_csrf(self):
        self.assertEqual(self.app.test_client().get('/api/boards').status_code, 401)
        self.assertEqual(self.client.post('/api/boards', json={'name': 'Forged'}).status_code, 403)
        cross = self.client.post('/api/login', json={'username': 'Monke', 'password': 'admin-password-123'},
                                 headers={'Origin': 'https://evil.example'})
        self.assertEqual(cross.status_code, 403)
        login = self.app.test_client().post('/api/login', json={'username': 'Monke', 'password': 'admin-password-123'})
        cookie = login.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertNotIn('password_hash', login.get_data(as_text=True))
        self.assertEqual(self.request('POST', '/api/logout', {}).status_code, 200)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_login_throttling_is_persistent(self):
        client = self.app.test_client()
        for _ in range(10):
            self.assertEqual(client.post('/api/login', json={'username': 'missing', 'password': 'wrong'}).status_code, 401)
        restarted = create_app(self.config).test_client()
        self.assertEqual(restarted.post('/api/login', json={'username': 'missing', 'password': 'wrong'}).status_code, 429)

    def test_persistence_and_sessions_after_restart(self):
        task_id = self.task()
        cookie = self.client.get_cookie('kanban_session').value
        app = create_app(self.config)
        client = app.test_client()
        client.set_cookie('kanban_session', cookie)
        self.assertEqual(client.get('/api/me').status_code, 200)
        board = client.get('/api/boards/' + self.board_id).json['board']
        self.assertEqual(board['tasks'][0]['id'], task_id)
        self.assertEqual(board['tasks'][0]['description'], 'Details')
        self.assertEqual(client.post('/api/login', json={'username': 'Monke', 'password': 'monkeman'}).status_code, 401)
        with closing(connect(self.path)) as db:
            stored = db.execute('SELECT token_hash FROM sessions LIMIT 1').fetchone()[0]
            self.assertNotEqual(stored, cookie)

    def test_viewer_is_read_only_for_all_mutations(self):
        self.create_user(role='Viewer')
        client, csrf = self.login_user()
        task_id = self.task()
        board = self.board()
        column = board['columns'][0]['id']
        version = board['version']
        endpoints = [
            ('POST', '/api/boards', {'name': 'Denied'}),
            ('PATCH', '/api/boards/' + self.board_id, {'name': 'Denied', 'version': version}),
            ('DELETE', '/api/boards/' + self.board_id, {'version': version}),
            ('POST', f'/api/boards/{self.board_id}/columns', {'name': 'Denied', 'version': version}),
            ('PATCH', '/api/columns/' + column, {'name': 'Denied', 'version': version}),
            ('DELETE', '/api/columns/' + column, {'target_id': board['columns'][1]['id'], 'version': version}),
            ('POST', '/api/columns/' + column + '/move', {'position': 1, 'version': version}),
            ('POST', f'/api/boards/{self.board_id}/tasks', {'title': 'Denied', 'version': version}),
            ('PATCH', '/api/tasks/' + task_id, {'title': 'Denied', 'version': version}),
            ('POST', '/api/tasks/' + task_id + '/move', {'column_id': column, 'version': version}),
            ('DELETE', '/api/tasks/' + task_id, {'version': version}),
            ('POST', '/api/users', {'username': 'denied', 'password': 'temporary-password'}),
            ('POST', '/api/import', {'board': {'columns': [], 'tasks': []}}),
        ]
        for method, path, data in endpoints:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.request(method, path, data, client, csrf).status_code, 403)
        self.assertEqual(self.upload(task_id, 'note.txt', b'hello', client, csrf).status_code, 403)
        self.assertEqual(client.get('/api/users').status_code, 403)
        self.assertEqual(client.get('/api/boards').status_code, 200)

    def test_member_manager_and_individual_permissions(self):
        member = self.create_user()
        client, csrf = self.login_user()
        task_id = self.task(client, csrf)
        board = self.board()
        result = self.request('POST', '/api/tasks/' + task_id + '/move',
            {'column_id': board['columns'][1]['id'], 'version': board['version']}, client, csrf)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.request('DELETE', '/api/tasks/' + task_id,
            {'version': self.board()['version']}, client, csrf).status_code, 403)
        current = next(u for u in self.client.get('/api/users').json['users'] if u['id'] == member['id'])
        result = self.request('PATCH', '/api/users/' + member['id'], {
            'version': current['version'], 'role': 'Manager', 'overrides': {'delete_columns': False, 'manage_users': True}})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('delete_columns', result.json['user']['permissions'])
        self.assertIn('manage_users', result.json['user']['permissions'])
        self.assertEqual(client.get('/api/me').status_code, 401)
        login = client.post('/api/login', json={'username': 'other', 'password': 'changed-password-123'})
        self.assertEqual(login.status_code, 200)
        csrf = login.json['csrf']
        self.assertEqual(self.request('DELETE', '/api/tasks/' + task_id,
            {'version': self.board()['version']}, client, csrf).status_code, 200)
        self.assertEqual(self.request('POST', '/api/users', {'username': 'escalation',
            'password': 'temporary-password', 'role': 'Administrator'}, client, csrf).status_code, 403)

    def test_no_self_escalation_and_last_administrator_protection(self):
        result = self.request('PATCH', '/api/users/' + self.admin['id'], {
            'version': self.admin['version'], 'role': 'Viewer'})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.request('DELETE', '/api/users/' + self.admin['id'],
            {'version': self.admin['version']}).status_code, 403)
        account = self.create_user(role='Manager', overrides={'manage_users': True, 'manage_permissions': True})
        client, csrf = self.login_user()
        current = client.get('/api/me').json['user']
        self.assertEqual(self.request('PATCH', '/api/users/' + account['id'], {
            'version': current['version'], 'role': 'Administrator'}, client, csrf).status_code, 403)
        self.assertEqual(self.request('DELETE', '/api/users/' + self.admin['id'],
            {'version': self.admin['version']}, client, csrf).status_code, 403)

    def test_disable_and_password_reset_revoke_sessions(self):
        account = self.create_user()
        client, csrf = self.login_user()
        current = next(u for u in self.client.get('/api/users').json['users'] if u['id'] == account['id'])
        result = self.request('POST', '/api/users/' + account['id'] + '/password', {
            'version': current['version'], 'password': 'reset-password-123'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get('/api/me').status_code, 401)
        login = client.post('/api/login', json={'username': 'other', 'password': 'reset-password-123'})
        self.assertTrue(login.json['user']['must_change_password'])
        result = self.request('PATCH', '/api/users/' + account['id'],
            {'version': result.json['user']['version'], 'enabled': False})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get('/api/me').status_code, 401)
        self.assertEqual(client.post('/api/login', json={'username': 'other', 'password': 'reset-password-123'}).status_code, 401)

    def test_stale_writes_do_not_overwrite_and_transactions_rollback(self):
        board = self.board()
        data = {'version': board['version'], 'name': 'New column', 'type': 'doing'}
        self.assertEqual(self.request('POST', f'/api/boards/{self.board_id}/columns', data).status_code, 201)
        self.assertEqual(self.request('POST', f'/api/boards/{self.board_id}/columns', {**data, 'name': 'Stale'}).status_code, 409)
        board = self.board()
        bad_task = {'version': board['version'], 'column_id': board['columns'][0]['id'],
                    'title': 'Rollback', 'assignees': ['missing-user']}
        self.assertEqual(self.request('POST', f'/api/boards/{self.board_id}/tasks', bad_task).status_code, 404)
        self.assertFalse(self.board()['tasks'])
        self.assertEqual(self.board()['version'], board['version'])

    def test_simultaneous_edits_one_success_one_conflict(self):
        version = self.board()['version']
        cookie = self.client.get_cookie('kanban_session').value
        def write(name):
            client = self.app.test_client()
            client.set_cookie('kanban_session', cookie)
            return self.request('POST', f'/api/boards/{self.board_id}/columns',
                {'version': version, 'name': name, 'type': 'todo'}, client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, ['First concurrent edit', 'Second concurrent edit']))
        self.assertEqual(sorted(outcomes), [201, 409])
        self.assertEqual(len(self.board()['columns']), 4)

    def test_task_and_column_order_persist_and_last_column_remains(self):
        first, second = self.task(), self.task()
        board = self.board()
        first_col, second_col = [c['id'] for c in board['columns'][:2]]
        result = self.request('POST', '/api/tasks/' + second + '/move', {
            'version': board['version'], 'column_id': first_col, 'before_id': first})
        self.assertEqual(result.status_code, 200)
        self.assertEqual([t['id'] for t in result.json['board']['tasks']], [second, first])
        result = self.request('POST', '/api/columns/' + first_col + '/move',
            {'version': result.json['board']['version'], 'position': 2})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['board']['columns'][2]['id'], first_col)
        result = self.request('DELETE', '/api/columns/' + first_col,
            {'version': self.board()['version'], 'target_id': second_col})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(all(t['column_id'] == second_col for t in self.board()['tasks']))
        last = next(c['id'] for c in self.board()['columns'] if c['id'] != second_col)
        self.request('DELETE', '/api/columns/' + last, {'version': self.board()['version'], 'target_id': second_col})
        self.assertEqual(self.request('DELETE', '/api/columns/' + second_col,
            {'version': self.board()['version'], 'target_id': second_col}).status_code, 400)

    def test_attachments_dxf_bytes_restart_and_access_control(self):
        task = self.task()
        content = b'0\r\nSECTION\r\n2\r\nENTITIES\r\n0\r\nENDSEC\r\n0\r\nEOF\r\n'
        result = self.upload(task, 'drawing.dxf', content)
        self.assertEqual(result.status_code, 201, result.json)
        attachment = result.json['board']['tasks'][0]['attachments'][0]
        self.assertEqual(attachment['size'], len(content))
        self.assertEqual(attachment['sha256'], hashlib.sha256(content).hexdigest())
        path = '/api/attachments/' + attachment['id'] + '/download'
        self.assertEqual(self.app.test_client().get(path).status_code, 401)
        board = self.board()
        self.request('POST', '/api/tasks/' + task + '/move',
            {'version': board['version'], 'column_id': board['columns'][1]['id']})
        restarted = create_app(self.config).test_client()
        restarted.set_cookie('kanban_session', self.client.get_cookie('kanban_session').value)
        response = restarted.get(path)
        self.assertEqual(response.data, content)
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_file_validation_limits_and_atomic_batch_upload(self):
        task = self.task()
        for filename, content in [('bad.exe', b'executable'), ('fake.pdf', b'not pdf'),
                                  ('fake.dxf', b'not dxf'), ('empty.txt', b''), ('binary.txt', b'\x00')]:
            with self.subTest(filename=filename):
                self.assertEqual(self.upload(task, filename, content).status_code, 400)
        self.assertEqual(self.upload(task, 'large.txt', b'x' * 4097).status_code, 413)
        board = self.board()
        response = self.client.post(f'/api/tasks/{task}/attachments', data={
            'file': [(io.BytesIO(b'valid'), 'valid.txt'), (io.BytesIO(b'exe'), 'invalid.exe')]},
            headers={'X-CSRF-Token': self.csrf, 'If-Match': str(board['version'])})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.board()['tasks'][0]['attachments'])
        self.assertEqual(self.board()['version'], board['version'])

    def test_supported_file_formats_and_viewer_download_only(self):
        task = self.task()
        def archive(names):
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                for name in names:
                    z.writestr(name, '<xml/>')
            return data.getvalue()
        formats = {'note.txt': b'hello', 'paper.pdf': b'%PDF-1.7\n',
            'photo.jpg': b'\xff\xd8\xff\xe0', 'photo.jpeg': b'\xff\xd8\xff\xe0',
            'image.png': b'\x89PNG\r\n\x1a\n', 'old.doc': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',
            'old.xls': b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', 'files.zip': archive(['note.txt']),
            'new.docx': archive(['[Content_Types].xml', 'word/document.xml']),
            'new.xlsx': archive(['[Content_Types].xml', 'xl/workbook.xml']),
            'binary.dxf': b'AutoCAD Binary DXF\r\n\x1a\x00\x00\x00'}
        for name, content in formats.items():
            self.assertEqual(self.upload(task, name, content).status_code, 201, name)
        attachment = self.board()['tasks'][0]['attachments'][0]
        self.create_user(role='Viewer')
        viewer, csrf = self.login_user()
        self.assertEqual(viewer.get('/api/attachments/' + attachment['id'] + '/download').status_code, 200)
        self.assertEqual(self.request('DELETE', '/api/attachments/' + attachment['id'],
            {'version': self.board()['version']}, viewer, csrf).status_code, 403)

    def test_delete_user_preserves_tasks_and_attachments_and_relationships(self):
        account = self.create_user(role='Manager')
        client, csrf = self.login_user()
        task = self.task(client, csrf)
        result = self.upload(task, 'note.txt', b'keep me', client, csrf)
        self.assertEqual(result.status_code, 201)
        current = next(u for u in self.client.get('/api/users').json['users'] if u['id'] == account['id'])
        self.assertEqual(self.request('DELETE', '/api/users/' + account['id'], {'version': current['version']}).status_code, 200)
        board = self.board()
        self.assertEqual(len(board['tasks']), 1)
        self.assertIsNone(board['tasks'][0]['created_by'])
        attachment = board['tasks'][0]['attachments'][0]
        self.assertIsNone(attachment['uploaded_by'])
        self.assertEqual(self.client.get('/api/attachments/' + attachment['id'] + '/download').data, b'keep me')
        with closing(connect(self.path)) as db:
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_browser_import_idempotent_and_atomic(self):
        snapshot = {'columns': [['todo', 'Start', 'todo'], ['done', 'Finished', 'done']], 'tasks': [
            {'id': 'legacy', 'title': 'Imported', 'description': 'Keep details', 'priority': 'low', 'status': 'todo'}]}
        first = self.request('POST', '/api/import', {'board': snapshot})
        self.assertEqual(first.status_code, 201)
        second = self.request('POST', '/api/import', {'board': snapshot})
        self.assertEqual(second.json['board_id'], first.json['board_id'])
        self.assertEqual(len(self.client.get('/api/boards').json['boards']), 2)
        imported = self.client.get('/api/boards/' + first.json['board_id']).json['board']
        self.assertEqual(imported['tasks'][0]['description'], 'Keep details')
        invalid = {**snapshot, 'tasks': [{'title': 'bad', 'status': 'missing'}]}
        self.assertEqual(self.request('POST', '/api/import', {'board': invalid}).status_code, 400)
        self.assertEqual(len(self.client.get('/api/boards').json['boards']), 2)

    def test_user_versions_and_permission_validation(self):
        account = self.create_user()
        self.assertEqual(self.request('PATCH', '/api/users/' + account['id'],
            {'version': account['version'], 'overrides': {'unknown_permission': True}}).status_code, 400)
        self.assertEqual(self.request('PATCH', '/api/users/' + account['id'],
            {'version': account['version'], 'enabled': False}).status_code, 200)
        self.assertEqual(self.request('PATCH', '/api/users/' + account['id'],
            {'version': account['version'], 'enabled': True}).status_code, 409)

    def test_cross_board_moves_and_assignment_ownership(self):
        task = self.task()
        other = self.request('POST', '/api/boards', {'name': 'Another board'}).json['board']
        self.assertEqual(self.request('POST', '/api/tasks/' + task + '/move', {
            'version': self.board()['version'], 'column_id': other['columns'][0]['id']}).status_code, 400)
        account = self.create_user()
        result = self.request('PATCH', '/api/tasks/' + task, {'version': self.board()['version'],
            'title': 'Assigned', 'description': 'Details', 'priority': 'normal', 'assignees': [account['id']]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['board']['tasks'][0]['assignees'], [account['id']])
        self.assertEqual(self.request('DELETE', '/api/boards/' + other['id'], {'version': other['version']}).status_code, 200)

    def test_explicit_denial_of_board_access_applies_to_write_responses(self):
        self.create_user(role='Manager', overrides={'view_boards': False})
        client, csrf = self.login_user()
        task = self.task()
        self.assertEqual(client.get('/api/boards').status_code, 403)
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task, {
            'version': self.board()['version'], 'title': 'Cannot bypass read denial'}, client, csrf).status_code, 403)

    def test_backup_restores_all_data_and_never_overwrites(self):
        task = self.task()
        self.upload(task, 'backup.txt', b'backup payload')
        location = Path(self.temp.name) / 'backup.sqlite3'
        backup(self.path, location)
        with self.assertRaises(FileExistsError):
            backup(self.path, location)
        restored = create_app({**self.config, 'DATABASE': str(location)}).test_client()
        restored.set_cookie('kanban_session', self.client.get_cookie('kanban_session').value)
        board = restored.get('/api/boards/' + self.board_id).json['board']
        self.assertEqual(board['tasks'][0]['id'], task)
        attachment = board['tasks'][0]['attachments'][0]
        self.assertEqual(restored.get('/api/attachments/' + attachment['id'] + '/download').data, b'backup payload')

    def test_delegated_permissions_cannot_create_administrators(self):
        self.create_user(role='Manager', overrides={'manage_users': True, 'manage_permissions': True})
        client, csrf = self.login_user()
        self.assertEqual(self.request('POST', '/api/users', {'username': 'admin-bypass',
            'password': 'temporary-password', 'role': 'Administrator'}, client, csrf).status_code, 403)

    def test_attachment_delete_and_parent_delete_cascade(self):
        task = self.task()
        result = self.upload(task, 'delete.txt', b'delete me')
        attachment_id = result.json['board']['tasks'][0]['attachments'][0]['id']
        self.assertEqual(self.request('DELETE', '/api/attachments/' + attachment_id,
            {'version': self.board()['version']}).status_code, 200)
        self.assertEqual(self.client.get('/api/attachments/' + attachment_id + '/download').status_code, 404)
        self.upload(task, 'parent.txt', b'parent deletion')
        self.assertEqual(self.request('DELETE', '/api/tasks/' + task,
            {'version': self.board()['version']}).status_code, 200)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0], 0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__':
    unittest.main()
