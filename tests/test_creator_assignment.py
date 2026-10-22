import unittest

import test_app


class CreatorAssignmentTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    board = test_app.KanbanTests.board
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    def create(self, assignees=None, client=None, csrf=None):
        board = self.board()
        data = {'version': board['version'], 'column_id': board['columns'][0]['id'], 'title': 'Automatic responsibility'}
        if assignees is not None:
            data['assignees'] = assignees
        return self.request('POST', '/api/boards/' + self.board_id + '/tasks', data, client, csrf)

    def test_creator_is_added_with_omitted_empty_or_other_assignees(self):
        other = self.create_user()
        for assignees in (None, [], [other['id']], [self.admin['id'], other['id']]):
            response = self.create(assignees)
            self.assertEqual(response.status_code, 201, response.json)
            task = next(t for t in response.json['board']['tasks'] if t['id'] == response.json['task_id'])
            self.assertEqual(set(task['assignees']), {self.admin['id']} | set(assignees or []))
            self.assertTrue(task['assigned_to_me'])
            history = self.client.get('/api/tasks/' + task['id'] + '/history').json['entries']
            assignments = [entry for entry in history if entry['action'] == 'task.assigned' and entry['new']['user_id'] == self.admin['id']]
            self.assertEqual(len(assignments), 1)
            self.assertEqual(assignments[0]['task_number'], task['task_number'])

    def test_creator_without_assignment_permissions_is_still_responsible(self):
        account = self.create_user(role='Member', overrides={'create_tasks': True, 'assign_self': False, 'assign_users': False})
        client, csrf = self.login_user()
        response = self.create([], client, csrf)
        self.assertEqual(response.status_code, 201, response.json)
        task = next(t for t in response.json['board']['tasks'] if t['id'] == response.json['task_id'])
        self.assertEqual(task['assignees'], [account['id']])
        self.assertTrue(task['assigned_to_me'])
        self.assertEqual(self.create([self.admin['id']], client, csrf).status_code, 403)

    def test_later_authorized_removal_is_preserved(self):
        response = self.create()
        task_id = response.json['task_id']
        response = self.request('PATCH', '/api/tasks/' + task_id, {
            'version': self.board()['version'], 'title': 'Reassigned later', 'assignees': []})
        self.assertEqual(response.status_code, 200, response.json)
        task = next(t for t in self.board()['tasks'] if t['id'] == task_id)
        self.assertEqual(task['assignees'], [])
        self.assertFalse(task['assigned_to_me'])
