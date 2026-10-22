import io
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta

from app import ROLES, connect, create_app, effective
import test_app


class ActivityTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    board = test_app.KanbanTests.board
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user
    task = test_app.KanbanTests.task
    upload = test_app.KanbanTests.upload

    def logs(self, query=''):
        result = self.client.get('/api/activity' + query)
        self.assertEqual(result.status_code, 200, result.json)
        return result.json['entries']

    def test_task_lifecycle_assignments_and_exact_snapshots(self):
        account = self.create_user()
        task_id = self.task()
        result = self.request('PATCH', '/api/tasks/' + task_id, {
            'version': self.board()['version'], 'title': 'Renamed task', 'description': 'Updated description',
            'priority': 'low', 'assignees': [account['id']]})
        self.assertEqual(result.status_code, 200)
        cols = self.board()['columns']
        result = self.request('POST', '/api/tasks/' + task_id + '/move', {
            'version': self.board()['version'], 'column_id': cols[1]['id']})
        self.assertEqual(result.status_code, 200)
        result = self.request('PATCH', '/api/tasks/' + task_id + '/assignees', {
            'version': self.board()['version'], 'remove': [account['id']]})
        self.assertEqual(result.status_code, 200)
        self.request('DELETE', '/api/tasks/' + task_id, {'version': self.board()['version']})
        history = self.client.get('/api/tasks/' + task_id + '/history').json['entries']
        self.assertEqual([e['action'] for e in reversed(history)], [
            'task.created', 'task.updated', 'task.assigned', 'task.moved', 'task.unassigned', 'task.deleted'])
        changed = next(e for e in history if e['action'] == 'task.updated')
        self.assertEqual(changed['previous']['description'], 'Details')
        self.assertEqual(changed['new']['description'], 'Updated description')
        moved = next(e for e in history if e['action'] == 'task.moved')
        self.assertEqual(moved['previous']['column_name'], cols[0]['name'])
        self.assertEqual(moved['new']['column_name'], cols[1]['name'])
        self.assertEqual(moved['actor_name'], 'Admin')
        self.assertIsNotNone(datetime.fromisoformat(moved['occurred_at']).tzinfo)

    def test_columns_boards_import_and_cascaded_file_deletion_are_logged(self):
        board = self.request('POST', '/api/boards', {'name': 'Audit board'}).json['board']
        board_id = board['id']
        edited = self.request('PATCH', '/api/boards/' + board_id, {'name': 'New board name', 'version': board['version']}).json['board']
        created = self.request('POST', f'/api/boards/{board_id}/columns',
            {'name': 'Review', 'type': 'doing', 'version': edited['version']}).json['board']
        col = created['columns'][-1]
        renamed = self.request('PATCH', '/api/columns/' + col['id'],
            {'name': 'Reviewed', 'type': 'done', 'version': created['version']}).json['board']
        reordered = self.request('POST', '/api/columns/' + col['id'] + '/move',
            {'position': 0, 'version': renamed['version']}).json['board']
        self.assertEqual(self.request('DELETE', '/api/columns/' + col['id'],
            {'target_id': created['columns'][0]['id'], 'version': reordered['version']}).status_code, 200)
        current = self.client.get('/api/boards/' + board_id).json['board']
        result = self.request('POST', f'/api/boards/{board_id}/tasks',
            {'title': 'Cascade task', 'column_id': current['columns'][0]['id'], 'version': current['version']})
        task_id = result.json['task_id']
        self.assertEqual(self.upload(task_id, 'cascade.txt', b'keep metadata', version=result.json['board']['version']).status_code, 201)
        current = self.client.get('/api/boards/' + board_id).json['board']
        self.assertEqual(self.request('DELETE', '/api/boards/' + board_id, {'version': current['version']}).status_code, 200)
        actions = {e['action'] for e in self.logs() if e['board_id'] == board_id}
        self.assertTrue({'board.created', 'board.updated', 'board.deleted', 'column.created', 'column.updated',
                         'column.reordered', 'column.deleted', 'task.created', 'task.deleted',
                         'attachment.uploaded', 'attachment.deleted'} <= actions)
        entry = next(e for e in self.logs() if e['action'] == 'attachment.deleted')
        self.assertEqual(entry['previous']['filename'], 'cascade.txt')
        self.assertEqual(entry['previous']['uploader'], 'Admin')
        self.assertEqual(entry['board_name'], 'New board name')

    def test_user_roles_permissions_logs_have_no_credentials(self):
        account = self.create_user()
        result = self.request('PATCH', '/api/users/' + account['id'], {
            'version': account['version'], 'role': 'Viewer', 'overrides': {'view_activity_log': True}, 'enabled': False})
        self.assertEqual(result.status_code, 200)
        self.request('POST', '/api/users/' + account['id'] + '/password',
            {'version': result.json['user']['version'], 'password': 'reset-password-secret'})
        current = next(u for u in self.client.get('/api/users').json['users'] if u['id'] == account['id'])
        self.request('DELETE', '/api/users/' + account['id'], {'version': current['version']})
        entries = [e for e in self.logs() if e['entity_id'] == account['id']]
        self.assertEqual({e['action'] for e in entries}, {'user.created', 'user.updated', 'user.role_changed',
            'user.permissions_changed', 'user.deleted', 'user.password_reset'})
        changed = next(e for e in entries if e['action'] == 'user.permissions_changed')
        self.assertEqual(changed['new']['overrides'], {'view_activity_log': True})
        self.assertEqual(changed['actor_name'], 'Admin')
        serialized = json.dumps(entries)
        for secret in ['temporary-password', 'reset-password-secret', 'password_hash', 'token_hash', 'csrf', '$argon2', self.csrf]:
            self.assertNotIn(secret, serialized)

    def test_actor_name_and_file_metadata_survive_deletion_and_restart(self):
        account = self.create_user(role='Manager')
        client, csrf = self.login_user()
        task_id = self.task(client, csrf)
        result = self.upload(task_id, 'original.txt', b'unchanged bytes', client, csrf)
        attachment = result.json['board']['tasks'][0]['attachments'][0]
        self.assertEqual(self.request('DELETE', '/api/attachments/' + attachment['id'],
            {'version': self.board()['version']}, client, csrf).status_code, 200)
        current = next(u for u in self.client.get('/api/users').json['users'] if u['id'] == account['id'])
        self.request('DELETE', '/api/users/' + account['id'], {'version': current['version']})
        self.request('DELETE', '/api/tasks/' + task_id, {'version': self.board()['version']})
        restarted = create_app(self.config).test_client()
        restarted.set_cookie('kanban_session', self.client.get_cookie('kanban_session').value)
        history = restarted.get('/api/tasks/' + task_id + '/history').json['entries']
        deletion = next(e for e in history if e['action'] == 'attachment.deleted')
        self.assertEqual(deletion['actor_name'], 'other')
        self.assertEqual(deletion['previous']['filename'], 'original.txt')
        self.assertEqual(deletion['previous']['size'], len(b'unchanged bytes'))
        self.assertEqual(deletion['previous']['uploader'], 'other')
        self.assertTrue(deletion['occurred_at'])
        self.assertEqual(restarted.get('/api/attachments/' + attachment['id'] + '/download').status_code, 404)

    def test_upload_only_and_own_delete_permissions(self):
        self.create_user(role='Viewer', overrides={'upload_attachments': True})
        uploader, token = self.login_user()
        task_id = self.task()
        uploaded = self.upload(task_id, 'only-upload.txt', b'upload only', uploader, token)
        attachment = uploaded.json['board']['tasks'][0]['attachments'][0]
        self.assertEqual(self.request('DELETE', '/api/attachments/' + attachment['id'],
            {'version': self.board()['version']}, uploader, token).status_code, 403)
        self.create_user('member', role='Member')
        member, token = self.login_user('member')
        result = self.upload(task_id, 'member.txt', b'own file', member, token)
        own = next(a for a in result.json['board']['tasks'][0]['attachments'] if a['filename'] == 'member.txt')
        self.assertEqual(self.request('DELETE', '/api/attachments/' + attachment['id'],
            {'version': self.board()['version']}, member, token).status_code, 403)
        self.assertEqual(self.request('DELETE', '/api/attachments/' + own['id'],
            {'version': self.board()['version']}, member, token).status_code, 200)

    def test_download_denial_also_hides_metadata_and_file_history(self):
        task_id = self.task()
        uploaded = self.upload(task_id, 'private-name.txt', b'private')
        attachment = uploaded.json['board']['tasks'][0]['attachments'][0]
        self.create_user(role='Manager', overrides={'download_attachments': False})
        client, _ = self.login_user()
        response = client.get('/api/boards/' + self.board_id)
        self.assertEqual(response.json['board']['tasks'][0]['attachments'], [])
        self.assertNotIn('private-name.txt', response.get_data(as_text=True))
        self.assertEqual(client.get('/api/attachments/' + attachment['id'] + '/download').status_code, 403)
        self.assertNotIn('private-name.txt', client.get('/api/activity').get_data(as_text=True))
        self.assertNotIn('private-name.txt', client.get('/api/tasks/' + task_id + '/history').get_data(as_text=True))

    def test_parent_deletion_cannot_bypass_file_permissions(self):
        task_id = self.task()
        self.upload(task_id, 'admin.txt', b'keep')
        self.create_user(overrides={'delete_tasks': True})
        client, token = self.login_user()
        count = len(self.logs())
        self.assertEqual(self.request('DELETE', '/api/tasks/' + task_id,
            {'version': self.board()['version']}, client, token).status_code, 403)
        self.assertEqual(len(self.logs()), count)
        self.assertEqual(len(self.board()['tasks'][0]['attachments']), 1)

    def test_assignment_rights_are_independent_of_task_editing(self):
        account = self.create_user(overrides={'assign_self': False})
        client, token = self.login_user()
        task_id = self.task()
        version = self.board()['version']
        result = self.request('PATCH', '/api/tasks/' + task_id, {'version': version,
            'title': 'Should roll back', 'description': 'changed', 'assignees': [account['id']]}, client, token)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.board()['tasks'][0]['title'], 'Task')
        self.assertFalse(any(e['action'] == 'task.updated' for e in self.logs()))
        result = self.request('PATCH', '/api/tasks/' + task_id, {'version': version,
            'title': 'Allowed edit', 'description': 'changed'}, client, token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'add': [account['id']]}, client, token).status_code, 403)

    def test_self_only_cannot_remove_others_and_omitting_assignment_preserves_it(self):
        account = self.create_user()
        client, token = self.login_user()
        task_id = self.task()
        self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'add': [self.admin['id']]})
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'remove': [self.admin['id']]}, client, token).status_code, 403)
        result = self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'add': [account['id']]}, client, token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(self.board()['tasks'][0]['assignees']), {account['id'], self.admin['id']})
        self.request('PATCH', '/api/tasks/' + task_id, {'version': self.board()['version'],
            'title': 'Keep assignments', 'description': 'Details', 'priority': 'high'}, client, token)
        self.assertEqual(set(self.board()['tasks'][0]['assignees']), {account['id'], self.admin['id']})
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'remove': [account['id']]}, client, token).status_code, 200)

    def test_assignment_grant_without_edit_and_assignee_visibility(self):
        account = self.create_user(role='Viewer', overrides={'assign_self': True, 'view_task_assignees': False})
        client, token = self.login_user()
        task_id = self.task()
        self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'add': [self.admin['id']]})
        result = self.request('PATCH', '/api/tasks/' + task_id + '/assignees',
            {'version': self.board()['version'], 'add': [account['id']]}, client, token)
        self.assertEqual(result.status_code, 200)
        task = result.json['board']['tasks'][0]
        self.assertEqual(task['assignees'], [])
        self.assertTrue(task['assigned_to_me'])
        self.assertEqual(len(client.get('/api/people').json['users']), 1)
        self.assertFalse(any(e['action'] in ('task.assigned', 'task.unassigned')
                             for e in client.get('/api/tasks/' + task_id + '/history').json['entries']))
        self.assertEqual(self.request('PATCH', '/api/tasks/' + task_id,
            {'version': self.board()['version'], 'title': 'Forbidden'}, client, token).status_code, 403)

    def test_history_global_permissions_filters_pagination_and_read_only(self):
        task_id = self.task()
        self.create_user()
        member, token = self.login_user()
        self.assertEqual(member.get('/api/activity').status_code, 403)
        self.assertEqual(member.get('/api/activity/options').status_code, 403)
        self.assertEqual(member.get('/api/tasks/' + task_id + '/history').status_code, 200)
        self.create_user('global-only', role='Viewer', overrides={'view_activity_log': True, 'view_task_history': False})
        global_only, _ = self.login_user('global-only')
        self.assertEqual(global_only.get('/api/activity').status_code, 200)
        self.assertEqual(global_only.get('/api/tasks/' + task_id + '/history').status_code, 403)
        entries = self.logs('?task_id=' + task_id + '&action=task.created&actor_id=' + self.admin['id'])
        self.assertEqual(len(entries), 1)
        stamp = entries[0]['occurred_at']
        from urllib.parse import urlencode
        self.assertEqual(len(self.logs('?' + urlencode({'task_id': task_id, 'start': stamp}))), 1)
        self.assertEqual(len(self.logs('?' + urlencode({'task_id': task_id, 'end': stamp}))), 0)
        first = self.client.get('/api/activity?limit=1').json
        second = self.logs('?limit=1&before=' + str(first['next_cursor']))
        self.assertGreater(first['entries'][0]['id'], second[0]['id'])
        for bad in ['?limit=0', '?action=invalid', '?start=invalid', '?before=-1']:
            self.assertEqual(self.client.get('/api/activity' + bad).status_code, 400)
        self.assertEqual(self.request('DELETE', '/api/activity/1', {}, member, token).status_code, 404)
        self.assertEqual(self.request('POST', '/api/activity', {}, member, token).status_code, 405)

    def test_audit_failure_rolls_back_corresponding_mutation(self):
        version = self.board()['version']
        before = self.logs()
        with closing(connect(self.path)) as db:
            db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON activity_log BEGIN SELECT RAISE(ABORT,'test failure'); END")
        response = self.request('POST', f'/api/boards/{self.board_id}/columns',
            {'version': version, 'name': 'Must roll back', 'type': 'todo'})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.board()['version'], version)
        self.assertEqual(len(self.board()['columns']), 3)
        self.assertEqual(self.logs(), before)

    def test_failed_batch_does_not_leave_partial_activity(self):
        task_id = self.task()
        count = len(self.logs())
        response = self.client.post('/api/tasks/' + task_id + '/attachments',
            data={'file': [(io.BytesIO(b'valid'), 'first.txt'), (io.BytesIO(b''), 'empty.exe')]},
            headers={'X-CSRF-Token': self.csrf, 'If-Match': str(self.board()['version'])})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.logs()), count)
        self.assertEqual(self.board()['tasks'][0]['attachments'], [])

    def test_migration_preserves_data_and_explicit_permission_settings(self):
        task_id = self.task()
        self.upload(task_id, 'migration.txt', b'preserve bytes')
        account = self.create_user()
        with closing(connect(self.path)) as db:
            db.execute('DROP TABLE activity_log')
            for column in ['display_name','avatar','avatar_version','profile_version']:
                db.execute('ALTER TABLE users DROP COLUMN '+column)
            for field in ('task_date','desired_delivery','tags'):
                db.execute('ALTER TABLE tasks DROP COLUMN '+field)
            db.execute('DROP TABLE task_tags')
            db.execute('DROP TABLE tags')
            db.execute('PRAGMA user_version=1')
            legacy = {'upload_attachments': False, 'delete_attachments': False,
                      'edit_tasks': False, 'view_boards': False, 'manage_users': True}
            db.execute('UPDATE users SET permissions=? WHERE id=?', (json.dumps(legacy), account['id']))
        create_app(self.config)
        create_app(self.config)
        with closing(connect(self.path)) as db:
            target = db.execute('SELECT * FROM users WHERE id=?', (account['id'],)).fetchone()
            overrides = json.loads(target['permissions'])
            for key, value in legacy.items():
                self.assertEqual(overrides[key], value)
            for key in ['download_attachments', 'assign_users', 'assign_self', 'delete_own_attachments']:
                self.assertFalse(overrides[key])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT content FROM attachments').fetchone()[0], b'preserve bytes')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(self.board()['tasks'][0]['id'], task_id)
        self.assertEqual(self.logs(), [])

    def test_recommended_role_defaults(self):
        self.assertTrue({'upload_attachments', 'download_attachments', 'delete_own_attachments', 'assign_self',
                         'view_task_assignees', 'view_task_history'} <= set(ROLES['Member']))
        self.assertFalse({'assign_users', 'delete_attachments', 'view_activity_log'} & set(ROLES['Member']))
        self.assertTrue({'view_task_history', 'view_task_assignees', 'download_attachments'} <= set(ROLES['Viewer']))
        self.assertNotIn('upload_attachments', ROLES['Viewer'])


if __name__ == '__main__':
    unittest.main()
