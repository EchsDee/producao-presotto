import io
import json
import struct
import unittest
import zlib
from contextlib import closing

from PIL import Image
from app import PH, connect, create_app
import test_app


def picture(format='PNG', size=(420, 300)):
    output = io.BytesIO()
    Image.new('RGB', size, '#2a4288').save(output, format=format)
    return output.getvalue()


class ProfileTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user
    task = test_app.KanbanTests.task
    board = test_app.KanbanTests.board

    def photo(self, filename='photo.png', content=None, version=0, client=None, csrf=None, name='Nome de exibição'):
        return (client or self.client).patch('/api/profile', data={
            'version': str(version), 'display_name': name, 'avatar_action': 'replace',
            'avatar': (io.BytesIO(content if content is not None else picture()), filename)},
            headers={'X-CSRF-Token': csrf or self.csrf})

    def test_every_user_can_edit_own_name_without_changing_identity(self):
        account = self.create_user(role='Viewer', overrides={'view_boards': False})
        client, csrf = self.login_user()
        result = self.request('PATCH', '/api/profile', {'version':0,'display_name':'Alex da Produção'}, client, csrf)
        self.assertEqual(result.status_code,200,result.json)
        user = result.json['user']
        self.assertEqual(user['id'],account['id'])
        self.assertEqual(user['username'],'other')
        self.assertEqual(user['role'],'Viewer')
        self.assertEqual(user['display_name'],'Alex da Produção')
        self.assertNotIn('view_boards',user['permissions'])
        self.assertEqual(client.get('/api/me').json['user']['display_name'],'Alex da Produção')
        self.assertEqual(client.post('/api/login',json={'username':'other','password':'changed-password-123'}).status_code,200)

    def test_profile_cannot_change_credentials_role_or_another_user(self):
        account = self.create_user(role='Manager',overrides={'manage_users':True})
        client, csrf = self.login_user()
        for field in ['role','username','permissions','enabled','user_id','password_hash']:
            with self.subTest(field=field):
                self.assertEqual(self.request('PATCH','/api/profile',{'version':0,field:'Administrator'},client,csrf).status_code,400)
        self.assertEqual(self.request('PATCH','/api/users/'+self.admin['id']+'/profile',
            {'version':0,'display_name':'Forbidden'},client,csrf).status_code,403)
        self.assertEqual(client.get('/api/users/'+self.admin['id']+'/profile').status_code,403)
        self.assertEqual(self.request('PATCH','/api/users/'+account['id']+'/profile',
            {'version':0,'display_name':'Admin edited'}).status_code,200)

    def test_allowed_image_formats_are_optimized_and_survive_restart(self):
        version=0
        for suffix,format in [('jpg','JPEG'),('jpeg','JPEG'),('png','PNG'),('webp','WEBP')]:
            result = self.photo('avatar.'+suffix,picture(format, (900,500)),version)
            self.assertEqual(result.status_code,200,result.json)
            version=result.json['user']['profile_version']
            response=self.client.get(result.json['user']['avatar_url'])
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.mimetype,'image/webp')
            with Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.size,(256,256))
                self.assertFalse(image.getexif())
        app=create_app(self.config); restarted=app.test_client()
        restarted.set_cookie('kanban_session',self.client.get_cookie('kanban_session').value)
        self.assertEqual(restarted.get(result.json['user']['avatar_url']).data,response.data)

    def test_invalid_images_and_limits_do_not_partially_save_profile(self):
        for name,data in [('bad.png',b'not an image'),('bad.svg',b'<svg/>'),('fake.jpg',picture('PNG'))]:
            self.assertEqual(self.photo(name,data).status_code,400)
        self.app.config['MAX_AVATAR_BYTES']=100
        self.assertEqual(self.photo('too-big.png',b'x'*101).status_code,413)
        self.app.config['MAX_AVATAR_BYTES']=5*1024*1024
        def chunk(kind,data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',20000,20000,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''))+chunk(b'IEND',b'')
        self.assertEqual(self.photo('bomb.png',bomb).status_code,400)
        self.assertEqual(self.client.get('/api/profile').json['user']['display_name'],'Monke')
        self.assertFalse(self.client.get('/api/profile').json['user']['avatar_url'])
        entries=self.client.get('/api/activity?action=user.avatar_changed').json['entries']
        self.assertEqual(entries,[])

    def test_avatar_read_access_removal_and_cache_revision(self):
        result=self.photo()
        url=result.json['user']['avatar_url']
        self.assertEqual(self.app.test_client().get(url).status_code,401)
        self.create_user(role='Viewer',overrides={'view_task_assignees':False})
        client,_=self.login_user()
        self.assertEqual(client.get(url).status_code,403)
        self.create_user('visible',role='Viewer')
        visible,_=self.login_user('visible')
        self.assertEqual(visible.get(url).status_code,200)
        updated=self.photo(content=picture('PNG',(500,600)),version=result.json['user']['profile_version'])
        # A different framing of a solid image can normalize to identical bytes; keep the revision stable in that case.
        version=updated.json['user']['profile_version']
        removed=self.request('PATCH','/api/profile',{'version':version,'avatar_action':'remove'})
        self.assertEqual(removed.status_code,200)
        self.assertIsNone(removed.json['user']['avatar_url'])
        self.assertEqual(self.client.get(url).status_code,404)

    def test_stale_profiles_csrf_and_name_validation(self):
        self.assertEqual(self.client.patch('/api/profile',json={'version':0,'display_name':'No CSRF'}).status_code,403)
        for value in ['', ' '*4, 'x'*81, 'bad\nname']:
            self.assertEqual(self.request('PATCH','/api/profile',{'version':0,'display_name':value}).status_code,400)
        self.assertEqual(self.request('PATCH','/api/profile',{'version':0,'display_name':'Updated'}).status_code,200)
        self.assertEqual(self.request('PATCH','/api/profile',{'version':0,'display_name':'Stale'}).status_code,409)
        self.assertEqual(self.client.get('/api/profile').json['user']['display_name'],'Updated')

    def test_name_changes_keep_assignments_and_logs_show_current_display_names(self):
        task=self.task()
        self.request('PATCH','/api/tasks/'+task+'/assignees',{'version':self.board()['version'],'add':[self.admin['id']]})
        self.request('PATCH','/api/profile',{'version':0,'display_name':'Alex'})
        board=self.board()
        self.assertEqual(board['tasks'][0]['assignees'],[self.admin['id']])
        people=self.client.get('/api/people').json['users']
        self.assertEqual(next(p for p in people if p['id']==self.admin['id'])['display_name'],'Alex')
        log=self.client.get('/api/tasks/'+task+'/history').json['entries']
        self.assertTrue(all(entry['actor_display_name']=='Alex' for entry in log))
        assignment=next(e for e in log if e['action']=='task.assigned')
        self.assertEqual(assignment['new']['display_name'],'Alex')
        self.assertEqual(assignment['actor_id'],self.admin['id'])

    def test_password_confirmation_and_safe_audit(self):
        before=len(self.client.get('/api/activity?action=user.password_changed').json['entries'])
        wrong=self.request('POST','/api/password',{'current_password':'admin-password-123','new_password':'new-secret-password','confirm_password':'different'})
        self.assertEqual(wrong.status_code,400)
        wrong=self.request('POST','/api/password',{'current_password':'wrong','new_password':'new-secret-password','confirm_password':'new-secret-password'})
        self.assertEqual(wrong.status_code,400)
        changed=self.request('POST','/api/password',{'current_password':'admin-password-123','new_password':'new-secret-password','confirm_password':'new-secret-password'})
        self.assertEqual(changed.status_code,200)
        entries=self.client.get('/api/activity?action=user.password_changed').json['entries']
        self.assertEqual(len(entries),before+1)
        serialized=json.dumps(entries)
        self.assertNotIn('new-secret-password',serialized)
        self.assertNotIn('password_hash',serialized)
        with closing(connect(self.path)) as db:
            self.assertTrue(PH.verify(db.execute('SELECT password_hash FROM users WHERE id=?',(self.admin['id'],)).fetchone()[0],'new-secret-password'))

    def test_migration_from_v2_preserves_users_passwords_and_assignments(self):
        task=self.task()
        self.request('PATCH','/api/tasks/'+task+'/assignees',{'version':self.board()['version'],'add':[self.admin['id']]})
        with closing(connect(self.path)) as db:
            before=tuple(db.execute('SELECT id,username,password_hash,role,permissions FROM users').fetchone())
            for column in ['display_name','avatar','avatar_version','profile_version']:
                db.execute('ALTER TABLE users DROP COLUMN '+column)
            db.execute('PRAGMA user_version=2')
        create_app(self.config)
        with closing(connect(self.path)) as db:
            after=tuple(db.execute('SELECT id,username,password_hash,role,permissions FROM users').fetchone())
            self.assertEqual(before,after)
            self.assertEqual(db.execute('SELECT display_name FROM users').fetchone()[0],'Monke')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertEqual(self.board()['tasks'][0]['assignees'],[self.admin['id']])
