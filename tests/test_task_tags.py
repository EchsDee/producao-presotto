import json
import sqlite3
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
    upload = test_app.KanbanTests.upload

    def tag(self, name='Corte', **values):
        result=self.request('POST','/api/tags',{'name':name,**values})
        self.assertEqual(result.status_code,201,result.json)
        return result.json['tag']

    def apply(self, task, ids, client=None, csrf=None, version=None):
        return self.request('PATCH','/api/tasks/'+task+'/tags',{'version':self.board()['version'] if version is None else version,'tag_ids':ids},client,csrf)

    def test_assign_persistence_history_rename_and_conflicts(self):
        tag=self.tag(color='#ffcc00');task=self.task()
        applied=self.apply(task,[tag['id']])
        self.assertEqual(applied.status_code,200,applied.json)
        self.assertEqual(applied.json['board']['tasks'][0]['tags'],['Corte'])
        old_version=self.board()['version']
        result=self.request('PATCH','/api/tags/'+tag['id'],{'version':tag['version'],'name':'Laser','color':'#000000'})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.apply(task,[],version=old_version).status_code,409)
        create_app(self.config)
        saved=self.board()['tasks'][0]
        self.assertEqual(saved['tags'],['Laser'])
        self.assertEqual(saved['tag_details'][0]['color'],'#000000')
        self.assertEqual(self.client.get('/api/tags').json['tags'][0]['task_count'],1)
        self.assertEqual(self.apply(task,[]).status_code,200)
        history=self.client.get('/api/tasks/'+task+'/history').json['entries']
        self.assertTrue(any(e['action']=='task.tag_added' and e['new']['name']=='Corte' for e in history))
        self.assertTrue(any(e['action']=='task.tag_removed' and e['previous']['name']=='Laser' for e in history))

    def test_reject_arbitrary_names_invalid_ids_and_duplicate_names(self):
        tag=self.tag('Straße');task=self.task()
        self.assertEqual(self.request('POST','/api/tags',{'name':'STRASSE'}).status_code,409)
        for ids in ('Corte',None,[1],['missing'],['x']*21):
            result=self.apply(task,ids)
            self.assertEqual(result.status_code,400,result.json)
        result=self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Task','tags':['New arbitrary tag']})
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.board()['tasks'][0]['tags'],[])
        self.assertEqual(len(self.client.get('/api/tags').json['tags']),1)

    def test_inactive_assignments_retained_and_reactivation(self):
        tag=self.tag();task=self.task();self.apply(task,[tag['id']])
        result=self.request('PATCH','/api/tags/'+tag['id'],{'version':1,'active':False})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.board()['tasks'][0]['tag_ids'],[tag['id']])
        self.assertEqual(self.apply(task,[tag['id']]).status_code,200)
        other=self.task()
        self.assertEqual(self.apply(other,[tag['id']]).status_code,400)
        self.assertEqual(self.apply(task,[]).status_code,200)
        self.assertEqual(self.apply(task,[tag['id']]).status_code,400)
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':2,'active':True}).status_code,200)
        self.assertEqual(self.apply(task,[tag['id']]).status_code,200)

    def test_permissions_independent_of_task_editing_and_of_each_other(self):
        tag=self.tag();task=self.task()
        self.create_user(overrides={'edit_tasks':False,'remove_tags':False})
        client,csrf=self.login_user()
        self.assertEqual(self.apply(task,[tag['id']],client,csrf).status_code,200)
        self.assertEqual(self.apply(task,[],client,csrf).status_code,403)
        self.assertEqual(self.request('POST','/api/tags',{'name':'No'},client,csrf).status_code,403)
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':1,'name':'No'},client,csrf).status_code,403)
        self.assertEqual(self.request('DELETE','/api/tags/'+tag['id'],{'version':1},client,csrf).status_code,403)
        self.create_user('removeonly',overrides={'assign_tags':False,'edit_tasks':False})
        other,token=self.login_user('removeonly')
        self.assertEqual(self.apply(task,[],other,token).status_code,200)
        self.assertEqual(self.apply(task,[tag['id']],other,token).status_code,403)
        self.create_user('editor',overrides={'assign_tags':False})
        editor,token=self.login_user('editor')
        version=self.board()['version']
        result=self.request('PATCH','/api/tasks/'+task,{'version':version,'title':'Should roll back','tag_ids':[tag['id']]},editor,token)
        self.assertEqual(result.status_code,403)
        self.assertEqual(self.board()['tasks'][0]['title'],'Task')

    def test_status_and_edit_permissions_separate(self):
        tag=self.tag()
        self.create_user(overrides={'manage_tag_status':True})
        client,csrf=self.login_user()
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':1,'active':False},client,csrf).status_code,200)
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':2,'name':'Changed'},client,csrf).status_code,403)
        self.create_user('editor',overrides={'edit_tags':True})
        editor,token=self.login_user('editor')
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':2,'name':'Changed'},editor,token).status_code,200)
        self.assertEqual(self.request('PATCH','/api/tags/'+tag['id'],{'version':3,'active':True},editor,token).status_code,403)

    def test_view_permission_blocks_board_and_history_data(self):
        tag=self.tag();task=self.task();self.apply(task,[tag['id']])
        self.create_user(overrides={'view_tags':False})
        client,csrf=self.login_user()
        self.assertEqual(client.get('/api/tags').status_code,403)
        saved=client.get('/api/boards/'+self.board_id).json['board']['tasks'][0]
        self.assertEqual(saved['tags'],[]);self.assertEqual(saved['tag_ids'],[])
        entries=client.get('/api/tasks/'+task+'/history').json['entries']
        self.assertFalse(any(e['action']=='task.tag_added' for e in entries))
        self.assertTrue(all('tags' not in e['new'] and 'tags' not in e['previous'] for e in entries))

    def test_deletion_keeps_tasks_files_and_audit_metadata(self):
        tag=self.tag();task=self.task();self.apply(task,[tag['id']])
        attachment=self.upload(task,'preservar.dwg',b'original drawing bytes')
        self.assertEqual(attachment.status_code,201)
        self.assertEqual(self.request('DELETE','/api/tags/'+tag['id'],{'version':1}).status_code,200)
        self.assertEqual(self.board()['tasks'][0]['id'],task)
        self.assertEqual(self.board()['tasks'][0]['tags'],[])
        file_id=self.board()['tasks'][0]['attachments'][0]['id']
        self.assertEqual(self.client.get('/api/attachments/'+file_id+'/download').data,b'original drawing bytes')
        entries=self.client.get('/api/activity').json['entries']
        deletion=next(e for e in entries if e['action']=='tag.deleted')
        self.assertEqual(deletion['previous']['name'],'Corte')
        self.assertEqual(deletion['previous']['task_count'],1)
        self.assertTrue(any(e['action']=='task.tag_removed' for e in entries))
        with closing(connect(self.path)) as db:
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_migration_preserves_duplicates_associations_dates_and_overrides(self):
        first=self.task();second=self.task()
        account=self.create_user(overrides={'edit_tasks':False})
        with closing(connect(self.path)) as db:
            db.execute('DROP TABLE task_tags');db.execute('DROP TABLE tags')
            db.execute('UPDATE tasks SET tags=?,desired_delivery=? WHERE id=?',(json.dumps(['Corte','URGENTE','corte']),'2026-11-05',first))
            db.execute('UPDATE tasks SET tags=? WHERE id=?',(json.dumps(['corte','Outro']),second))
            test_app.remove_task_identifiers(db)
            db.execute('PRAGMA user_version=5')
        create_app(self.config);create_app(self.config)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tags').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_tags').fetchone()[0],4)
            self.assertEqual(json.loads(db.execute('SELECT permissions FROM users WHERE id=?',(account['id'],)).fetchone()[0]),{'edit_tasks':False})
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            with self.assertRaises(sqlite3.IntegrityError):db.execute('INSERT INTO task_tags VALUES(?,?)',(first,'missing'))
        tasks={task['id']:task for task in self.board()['tasks']}
        self.assertEqual(tasks[first]['desired_delivery'],'2026-11-05')
        self.assertEqual(set(tasks[first]['tags']),{'Corte','URGENTE'})
        self.assertIn('Corte',tasks[second]['tags'])

    def test_create_with_tags_and_update_without_touching_assignments(self):
        tag=self.tag();board=self.board()
        result=self.request('POST','/api/boards/'+self.board_id+'/tasks',{'version':board['version'],'column_id':board['columns'][0]['id'],'title':'Tagged','tag_ids':[tag['id']]})
        self.assertEqual(result.status_code,201,result.json)
        task=result.json['task_id']
        self.assertEqual(result.json['board']['tasks'][0]['tag_ids'],[tag['id']])
        result=self.request('PATCH','/api/tasks/'+task,{'version':self.board()['version'],'title':'Edited'})
        self.assertEqual(result.json['board']['tasks'][0]['tag_ids'],[tag['id']])
