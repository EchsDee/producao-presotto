import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import unittest
import sqlite3
import io
from contextlib import closing
from pathlib import Path

import test_updates
import test_app
from test_profiles import picture
from updater import read
from launcher import process_identity


class LauncherTests(unittest.TestCase):
    def test_finished_process_is_not_terminated_again(self):
        with subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']) as process:
            identity=process_identity(process.pid)
            self.assertTrue(identity)
            process.terminate()
            process.wait(timeout=5)
            self.assertIsNone(process_identity(process.pid,terminate_if=identity))

    setUp = test_updates.UpdateTests.setUp
    request = test_updates.UpdateTests.request
    configure = test_updates.UpdateTests.configure
    bundle = test_updates.UpdateTests.bundle
    release = test_updates.UpdateTests.release
    download = test_updates.UpdateTests.download
    directory = test_updates.UpdateTests.directory
    task = test_app.KanbanTests.task
    board = test_app.KanbanTests.board
    upload = test_app.KanbanTests.upload

    def tearDown(self):
        if hasattr(self, 'process'):
            try:
                self.http('/_update/stop', {}, health=True)
                self.process.wait(timeout=8)
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=8)
            child=read(self.directory/'child.json')
            if child and child.get('identity'):
                process_identity(child['pid'],terminate_if=child['identity'])
                time.sleep(.3)
        test_updates.UpdateTests.tearDown(self)

    def boot(self):
        if not hasattr(self,'port'):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        env={**os.environ,'KANBAN_DATABASE':self.path,'KANBAN_PORT':str(self.port),'KANBAN_HOST':'127.0.0.1'}
        env.pop('KANBAN_GITHUB_TOKEN',None)
        self.process=subprocess.Popen([sys.executable,'launcher.py'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def http(self,path,data=None,health=False):
        headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf,
                 'Cookie':'kanban_session='+self.client.get_cookie('kanban_session').value}
        if health:headers['X-Update-Health']=read(self.directory/'health-key.json','')
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)

    def wait_for(self,condition,timeout=40):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                if condition():return
            except Exception:pass
            if self.process.poll() is not None:
                self.fail('Supervisor exited: '+(self.directory/'server.log').read_text(errors='replace'))
            time.sleep(.15)
        self.fail('Timed out; log: '+(self.directory/'server.log').read_text(errors='replace'))

    def test_success_restart_and_rollback_preserve_new_data(self):
        task=self.task()
        self.assertEqual(self.upload(task,'original.dxf',b'0\r\nSECTION\r\n2\r\nENTITIES\r\n0\r\nENDSEC\r\n0\r\nEOF\r\n').status_code,201)
        self.assertEqual(self.client.patch('/api/profile',data={'version':'0','display_name':'Administrador de produção','avatar_action':'replace','avatar':(io.BytesIO(picture()),'perfil.png')},headers={'X-CSRF-Token':self.csrf}).status_code,200)
        self.configure();self.download(self.bundle())
        tables=('users','boards','columns','tasks','task_assignees','attachments','metadata')
        with closing(sqlite3.connect(self.path)) as db:
            original={table:db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
        self.boot()
        self.wait_for(lambda:self.http('/health',health=True)['version']==test_updates.CURRENT)
        self.http('/api/updates/install',{'confirm':True,'version':test_updates.TARGET})
        self.wait_for(lambda:len(read(self.directory/'history.json',[]))==1 and not (self.directory/'job.json').exists())
        self.assertEqual(self.http('/health',health=True)['version'],test_updates.TARGET)
        self.assertEqual(read(self.directory/'history.json')[0]['status'],'installed')
        with closing(sqlite3.connect(self.path)) as db:
            for table in tables:
                self.assertEqual(db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),original[table],table)
        board=self.http('/api/boards/'+self.board_id)['board']
        created=self.http('/api/boards/'+self.board_id+'/tasks',{'version':board['version'],'column_id':board['columns'][0]['id'],'title':'Criada depois da atualização','description':'Preservar no rollback','priority':'normal','assignees':[]})
        self.http('/api/updates/rollback',{'confirm':True,'version':test_updates.CURRENT})
        self.wait_for(lambda:len(read(self.directory/'history.json',[]))==2 and not (self.directory/'job.json').exists())
        self.assertEqual(self.http('/health',health=True)['version'],test_updates.CURRENT)
        board=self.http('/api/boards/'+self.board_id)['board']
        self.assertTrue(any(t['title']=='Criada depois da atualização' for t in board['tasks']))
        self.assertEqual(read(self.directory/'history.json')[-1]['status'],'rolled_back')

    def test_broken_release_restores_database_and_previous_code(self):
        self.configure();self.download(self.bundle(broken=True));self.boot()
        self.wait_for(lambda:self.http('/health',health=True)['version']==test_updates.CURRENT)
        self.http('/api/updates/install',{'confirm':True,'version':test_updates.TARGET})
        self.wait_for(lambda:len(read(self.directory/'history.json',[]))==1 and not (self.directory/'job.json').exists())
        self.assertEqual(self.http('/health',health=True)['version'],test_updates.CURRENT)
        self.assertEqual(read(self.directory/'history.json')[0]['status'],'failed')
        self.assertEqual(self.http('/api/boards')['boards'][0]['id'],self.board_id)
        self.assertTrue(list(self.directory.glob('before-*.sqlite3')))
        self.assertTrue(list(self.directory.glob('failed-*.sqlite3')))

    def test_interrupted_install_recovers_on_supervisor_restart(self):
        self.configure()
        raw=self.bundle()

        source=Path(self.temp.name)/'source'
        code=(source/'app.py').read_text(encoding='utf-8')
        code=code.replace("if __name__ == '__main__':", "if __name__ == '__main__':\n    time.sleep(12)")
        (source/'app.py').write_text(code,encoding='utf-8')
        from build_release import build
        build(source,Path(self.temp.name)/'slow.zip')
        self.download((Path(self.temp.name)/'slow.zip').read_bytes())
        self.boot();self.wait_for(lambda:self.http('/health',health=True)['version']==test_updates.CURRENT)
        self.http('/api/updates/install',{'confirm':True,'version':test_updates.TARGET})
        self.wait_for(lambda:(self.directory/'journal.json').exists() and (self.directory/'child.json').exists())
        self.process.kill();self.process.wait(timeout=5)
        self.boot()
        self.wait_for(lambda:len(read(self.directory/'history.json',[]))==1 and not (self.directory/'job.json').exists())
        self.assertEqual(self.http('/health',health=True)['version'],test_updates.CURRENT)
        self.assertIn('interrompida',read(self.directory/'history.json')[0]['message'])

    def test_failed_migration_recovers_previous_schema(self):
        self.configure();self.bundle()
        source=Path(self.temp.name)/'source'
        (source/'migrations/008_broken.sql').write_text('CREATE TABLE must_rollback(id INTEGER); INSERT INTO missing_table VALUES(1);')
        from build_release import build
        build(source,Path(self.temp.name)/'migration.zip')
        self.download((Path(self.temp.name)/'migration.zip').read_bytes());self.boot()
        self.wait_for(lambda:self.http('/health',health=True)['version']==test_updates.CURRENT)
        self.http('/api/updates/install',{'confirm':True,'version':test_updates.TARGET})
        self.wait_for(lambda:len(read(self.directory/'history.json',[]))==1 and not (self.directory/'job.json').exists())
        self.assertEqual(self.http('/health',health=True)['version'],test_updates.CURRENT)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='must_rollback'").fetchone())


if __name__=='__main__':unittest.main()
