import unittest
from contextlib import closing
import test_app
from app import connect, create_app


class TaskTagsTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    task = test_app.KanbanTests.task
    board = test_app.KanbanTests.board
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    def test_normalization_persistence_partial_updates_and_history(self):
        task=self.task()
        def edit(**values):
            return self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task',**values})
        saved=edit(tags=[' Corte ','Urgente','corte']).json['board']['tasks'][0]
        self.assertEqual(saved['tags'],['Corte','Urgente'])
        create_app(self.config)
        self.assertEqual(edit().json['board']['tasks'][0]['tags'],['Corte','Urgente'])
        history=self.client.get('/api/tasks/'+task+'/history').json['entries']
        self.assertTrue(any(e['new'].get('tags')==['Corte','Urgente'] and e['previous'].get('tags')==[] for e in history if e['action']=='task.updated'))
        self.assertEqual(edit(tags=[]).json['board']['tasks'][0]['tags'],[])

    def test_validation_and_permissions(self):
        task=self.task()
        for tags in ('Urgente',None,[1],[''],['x'*41],['x']*21,['a,b'],['a\nb']):
            version=self.board()['version']
            result=self.request('PATCH','/api/tasks/'+task,{'version':version,'title':'Task','tags':tags})
            self.assertEqual(result.status_code,400)
            self.assertEqual(self.board()['version'],version)
        self.create_user(role='Viewer');client,csrf=self.login_user()
        result=self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task','tags':['Urgente']},client,csrf)
        self.assertEqual(result.status_code,403)

    def test_migrate_existing_tasks_without_losing_dates(self):
        task=self.task()
        self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task','task_date':'2026-10-21','desired_delivery':'2026-11-05'})
        with closing(connect(self.path)) as db:
            db.execute('ALTER TABLE tasks DROP COLUMN tags')
            db.execute('PRAGMA user_version=4')
        create_app(self.config)
        saved=self.board()['tasks'][0]
        self.assertEqual(saved['id'],task)
        self.assertEqual(saved['tags'],[])
        self.assertEqual(saved['desired_delivery'],'2026-11-05')
