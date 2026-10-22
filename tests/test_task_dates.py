import unittest
from contextlib import closing
import test_app
from app import connect, create_app


class TaskDateTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    task = test_app.KanbanTests.task
    board = test_app.KanbanTests.board
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    def test_dates_persist_partial_edits_clear_and_audit(self):
        task=self.task()
        def edit(**dates):
            return self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task','description':'Details','priority':'high',**dates})
        self.assertEqual(edit(task_date='2026-10-21',desired_delivery='2026-11-05').status_code,200)
        create_app(self.config)
        saved=self.board()['tasks'][0]
        self.assertEqual(saved['task_date'],'2026-10-21')
        self.assertEqual(saved['desired_delivery'],'2026-11-05')
        self.assertEqual(edit().json['board']['tasks'][0]['desired_delivery'],'2026-11-05')
        history=self.client.get('/api/tasks/'+task+'/history').json['entries']
        changed=next(e for e in history if e['action']=='task.updated')
        self.assertIsNone(changed['previous']['desired_delivery'])
        self.assertEqual(changed['new']['desired_delivery'],'2026-11-05')
        self.assertIsNone(edit(task_date=None,desired_delivery='').json['board']['tasks'][0]['desired_delivery'])

    def test_invalid_dates_rollback_and_permissions(self):
        task=self.task()
        for value in ('2026-02-30','21/10/2026','2026-1-01','0000-01-01',123,True):
            version=self.board()['version']
            result=self.request('PATCH','/api/tasks/'+task,{'version':version,'title':'Task','desired_delivery':value})
            self.assertEqual(result.status_code,400)
            self.assertEqual(self.board()['version'],version)
        self.create_user(role='Viewer');client,csrf=self.login_user()
        result=self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task','task_date':'2026-10-21'},client,csrf)
        self.assertEqual(result.status_code,403)

    def test_migration_preserves_existing_task(self):
        task=self.task()
        with closing(connect(self.path)) as db:
            original=tuple(db.execute('SELECT id,title,description,position,created_at FROM tasks WHERE id=?',(task,)).fetchone())
            for field in ('task_date','desired_delivery','tags'):db.execute('ALTER TABLE tasks DROP COLUMN '+field)
            db.execute('DROP TABLE task_tags')
            db.execute('DROP TABLE tags')
            test_app.remove_task_identifiers(db)
            db.execute('PRAGMA user_version=3')
        create_app(self.config)
        with closing(connect(self.path)) as db:
            self.assertEqual(original,tuple(db.execute('SELECT id,title,description,position,created_at FROM tasks WHERE id=?',(task,)).fetchone()))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
        self.assertIsNone(self.board()['tasks'][0]['task_date'])
