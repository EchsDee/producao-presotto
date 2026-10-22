import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path
from http.cookiejar import CookieJar

from app import PH
from updater import atomic

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
ARTIFACT = ROOT / 'releases' / VERSION / f'ProducaoPresotto-{VERSION}-windows-x64.zip'


@unittest.skipUnless(os.name == 'nt' and ARTIFACT.exists(), 'Windows artifact required')
class WindowsPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='presotto package ')
        self.base = Path(self.temporary.name)
        with zipfile.ZipFile(ARTIFACT) as archive:
            names=archive.namelist()
            self.assertFalse(any('/data/updates/' in name or name.endswith('/.env') for name in names))
            archive.extractall(self.base)
        self.source = self.base / f'ProducaoPresotto-{VERSION}-windows-x64'
        self.package = self.source
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.env = {**os.environ,'KANBAN_PORT':str(self.port),'KANBAN_HOST':'127.0.0.1'}
        for name in ('KANBAN_DATABASE','KANBAN_HEALTH_KEY','KANBAN_MANAGED','KANBAN_GITHUB_TOKEN','KANBAN_PUBLIC_ORIGIN'):
            self.env.pop(name,None)
        self.cookies=CookieJar()
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def tearDown(self):
        try:self.control('stop')
        finally:self.temporary.cleanup()

    def control(self,action):
        subprocess.run([str(self.package/'runtime/python.exe'),str(self.package/'desktop.py'),action,'--no-browser'],env=self.env,cwd=self.package,check=True,timeout=40,capture_output=True,text=True)

    def api(self,path,data=None,method=None):
        headers={'Content-Type':'application/json'}
        if hasattr(self,'csrf'):headers['X-CSRF-Token']=self.csrf
        request=urllib.request.Request(f'http://127.0.0.1:{self.port}/api'+path,headers=headers,data=json.dumps(data).encode() if data is not None else None,method=method)
        with self.client.open(request,timeout=5) as response:return json.load(response)

    def login(self):
        result=self.api('/login',{'username':'Admin','password':'adminpasspres'})
        self.assertTrue(result['user']['must_change_password'])
        self.csrf=result['csrf']
        result=self.api('/password',{'current_password':'adminpasspres','new_password':'portable-test-password','confirm_password':'portable-test-password'})
        self.csrf=result['csrf']

    def test_portable_start_stop_persistence_and_embedded_update(self):
        with closing(sqlite3.connect(self.package/'data/kanban.sqlite3')) as db:
            row=db.execute("SELECT password_hash,role FROM users WHERE username='Admin'").fetchone()
            self.assertTrue(PH.verify(row[0],'adminpasspres'))
            self.assertEqual(row[1],'Administrator')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        self.control('start');self.login()
        boardid=self.api('/boards')['boards'][0]['id']
        board=self.api('/boards/'+boardid)['board']
        self.api('/tags',{'name':'Portable'})
        board=self.api('/boards/'+boardid)['board']
        self.api('/boards/'+boardid+'/tasks',{'version':board['version'],'column_id':board['columns'][0]['id'],'title':'Teste do pacote','tags':['Portable'],'desired_delivery':'2026-11-05'})
        self.control('stop');self.control('start')
        self.assertEqual(next(task for task in self.api('/boards/'+boardid)['board']['tasks'] if task['title']=='Teste do pacote')['tags'],['Portable'])
        self.control('start')
        directory=self.package/'data/updates'
        stage=directory/'staging'
        from launcher import snapshot
        snapshot(self.package,stage)
        target='.'.join([*VERSION.split('.')[:2],str(int(VERSION.split('.')[2])+1)])
        (stage/'VERSION').write_text(target)
        import hashlib
        files={p.relative_to(stage).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob('*') if p.is_file()}
        atomic(directory/'download.json',{'version':target,'repository':'EchsDee/producao-presotto','files':files})
        self.api('/updates/install',{'version':target,'confirm':True})
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            try:
                status=self.api('/updates')
                if status['current']==target and not (directory/'job.json').exists():break
            except Exception:pass
            time.sleep(.2)
        else:self.fail('Embedded update failed')
        self.assertEqual(next(task for task in self.api('/boards/'+boardid)['board']['tasks'] if task['title']=='Teste do pacote')['tags'],['Portable'])

    def test_installer_and_reinstall_keep_existing_database(self):
        target=self.base/'installed application'
        command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.source/'Instalar.ps1'),'-InstallDir',str(target),'-NoLaunch','-NoShortcuts','-Quiet']
        subprocess.run(command,env=self.env,check=True,timeout=90,capture_output=True,text=True)
        self.package=target
        self.control('start');self.login();self.control('stop')
        with closing(sqlite3.connect(target/'data/kanban.sqlite3')) as db:
            saved=db.execute("SELECT password_hash FROM users WHERE username='Admin'").fetchone()[0]
        subprocess.run(command,env=self.env,check=True,timeout=90,capture_output=True,text=True)
        with closing(sqlite3.connect(target/'data/kanban.sqlite3')) as db:
            self.assertEqual(saved,db.execute("SELECT password_hash FROM users WHERE username='Admin'").fetchone()[0])
            self.assertTrue(PH.verify(saved,'portable-test-password'))
        self.control('start')
        self.assertEqual(self.api('/me')['user']['username'],'Admin')

    def test_exe_extracts_and_installs_seed_database(self):
        extract=self.base/'exe payload'
        extract.mkdir()
        installer=ROOT/'releases'/VERSION/f'ProducaoPresotto-{VERSION}-Setup.exe'
        subprocess.run([str(installer),'/Q','/T:'+str(extract),'/C'],check=True,timeout=45,capture_output=True)
        import hashlib
        self.assertEqual(hashlib.sha256((extract/'payload.zip').read_bytes()).digest(),hashlib.sha256(ARTIFACT.read_bytes()).digest())
        target=self.base/'exe installed application'
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(extract/'Instalar.ps1'),'-InstallDir',str(target),'-NoLaunch','-NoShortcuts','-Quiet'],env=self.env,check=True,timeout=90,capture_output=True,text=True)
        self.package=target
        self.control('start');self.login()

    def test_another_installation_uses_a_free_port(self):
        reserved=socket.socket()
        try:
            reserved.bind(('127.0.0.1',8000));reserved.listen()
        except OSError:
            reserved.close();reserved=None
        try:
            self.env.pop('KANBAN_PORT')
            self.control('start')
            self.port=json.loads((self.package/'data/updates/desktop.json').read_text())['port']
            self.assertNotEqual(self.port,8000)
            self.login()
        finally:
            if reserved:reserved.close()
