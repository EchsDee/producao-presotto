import hashlib
import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import test_app
import updater
from build_release import build
from app import connect, create_app


CURRENT = (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
_parts = CURRENT.split('-')[0].split('.')
TARGET = '.'.join([*_parts[:2], str(int(_parts[2]) + 1)])


class UpdateTests(unittest.TestCase):
    setUp = test_app.KanbanTests.setUp
    tearDown = test_app.KanbanTests.tearDown
    request = test_app.KanbanTests.request
    create_user = test_app.KanbanTests.create_user
    login_user = test_app.KanbanTests.login_user

    @property
    def directory(self):
        return Path(self.path).parent / 'updates'

    def configure(self):
        result = self.request('PATCH', '/api/updates/settings', {'version':0, 'owner':'presotto', 'repository':'kanban', 'channel':'stable'})
        self.assertEqual(result.status_code, 200, result.json)

    def bundle(self, broken=False):
        import shutil
        source = Path(self.temp.name) / ('broken' if broken else 'source')
        from launcher import snapshot
        snapshot(Path(__file__).resolve().parents[1], source)
        (source / 'VERSION').write_text(TARGET)
        if broken:
            (source / 'app.py').write_text("raise RuntimeError('broken release')")
        output = Path(self.temp.name) / 'bundle.zip'
        build(source, output)
        return output.read_bytes()

    def release(self, raw):
        return {'tag_name':'v'+TARGET,'draft':False,'prerelease':False,'body':'Notas <script>alert(1)</script>',
                'published_at':'2026-10-21T12:00:00Z', 'assets':[{'id':123,'name':'presotto-update.zip',
                'digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'size':len(raw)}]}

    def download(self, raw):
        with patch('updater.github', return_value=[self.release(raw)]):
            self.assertEqual(self.request('POST','/api/updates/check',{}).status_code,200)
        with patch('updater.github', return_value=raw):
            result=self.request('POST','/api/updates/download',{})
        self.assertEqual(result.status_code,200,result.json)

    def test_client_runtime_without_waitress(self):
        from app import serve_application
        with patch.dict('sys.modules', {'waitress': None}), patch('werkzeug.serving.run_simple') as serve:
            serve_application(self.app, '127.0.0.1', 8000)
        serve.assert_called_once_with('127.0.0.1', 8000, self.app,
                                      threaded=True, use_reloader=False, use_debugger=False)

    def test_semver(self):
        values=['1.0.1-alpha','1.0.1-alpha.2','1.0.1-alpha.10','1.0.1-beta','1.0.1-rc.1','1.0.1','1.0.2','2.0.0']
        self.assertEqual(sorted(values,key=updater.version),values)
        for invalid in ('1.0','1.02.0','1.0.1-01','1.0.1-a..b','1.0.1;whoami'):
            with self.assertRaises(ValueError): updater.version(invalid)

    def test_default_repository_and_opt_in_cli_credential(self):
        settings=self.client.get('/api/updates').json['settings']
        self.assertEqual((settings['owner'],settings['repository']),('EchsDee','producao-presotto'))
        import subprocess
        with patch.dict(os.environ,{'KANBAN_GITHUB_TOKEN':''}):
            with patch('subprocess.run') as run:
                self.assertEqual(updater.github_token(self.directory),'')
                run.assert_not_called()
            updater.atomic(self.directory/'auth.json',{'source':'github_cli'})
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'cli-private-secret\n','')) as run:
                response=self.client.get('/api/updates')
                self.assertTrue(response.json['token_configured'])
                self.assertEqual(response.json['token_source'],'GitHub CLI')
                self.assertNotIn('cli-private-secret',response.get_data(as_text=True))
                self.assertEqual(run.call_args.args[0],['gh','auth','token','--hostname','github.com'])
            with patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'','')):
                self.assertFalse(self.client.get('/api/updates').json['token_configured'])
        with patch.dict(os.environ,{'KANBAN_GITHUB_TOKEN':'environment-secret'}),patch('subprocess.run') as run:
            self.assertEqual(updater.github_token(self.directory),'environment-secret')
            run.assert_not_called()

    def test_auth_permissions_and_no_command_or_token_input(self):
        self.assertEqual(self.app.test_client().get('/api/updates').status_code,401)
        account=self.create_user(role='Manager')
        client,csrf=self.login_user()
        for method,path in [('GET',''),('GET','/history'),('GET','/progress'),('POST','/check'),('POST','/download'),('POST','/install'),('PATCH','/settings')]:
            self.assertEqual(self.request(method,'/api/updates'+path,{},client,csrf).status_code,403)
        self.assertEqual(self.request('POST','/api/updates/install',{'confirm':True,'command':'whoami'}).status_code,409)
        self.assertEqual(self.client.post('/_update/stop').status_code,404)
        self.assertEqual(self.client.get('/health').status_code,404)
        self.assertEqual(self.client.post('/api/updates/check',json={}).status_code,403)
        with patch.dict(os.environ,{'KANBAN_GITHUB_TOKEN':'private-secret-test'}):
            response=self.client.get('/api/updates')
            self.assertTrue(response.json['token_configured'])
            self.assertNotIn('private-secret-test',response.get_data(as_text=True))

    def test_settings_persist_conflict_trusted_paths(self):
        self.configure()
        response=create_app(self.config).test_client().get('/')
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertEqual(self.client.get('/api/updates').json['settings']['repository'],'kanban')
        self.assertEqual(self.request('PATCH','/api/updates/settings',{'version':0,'owner':'x','repository':'x','channel':'stable'}).status_code,409)
        for owner in ('../bad','https://evil.test','user/x'):
            self.assertEqual(self.request('PATCH','/api/updates/settings',{'version':1,'owner':owner,'repository':'x','channel':'stable'}).status_code,400)
        entries=self.client.get('/api/activity?action=update.configured').json['entries']
        self.assertEqual(len(entries),1)

    def test_release_download_integrity_and_install_confirmation(self):
        self.configure()
        raw=self.bundle()
        self.download(raw)
        self.assertEqual((self.directory/'staging/VERSION').read_text(),TARGET)
        self.assertEqual(self.client.get('/api/updates').json['downloaded']['version'],TARGET)
        self.assertFalse((self.directory/'staging/data').exists())
        with patch.dict(os.environ,{'KANBAN_MANAGED':'1'}):
            self.assertEqual(self.request('POST','/api/updates/install',{'version':TARGET}).status_code,400)
            self.assertEqual(self.request('POST','/api/updates/install',{'version':CURRENT,'confirm':True}).status_code,400)
            self.assertEqual(self.request('POST','/api/updates/install',{'version':TARGET,'confirm':True}).status_code,202)
            self.assertEqual(self.request('POST','/api/updates/install',{'version':TARGET,'confirm':True}).status_code,503)
            self.assertEqual(self.request('PATCH','/api/boards/'+self.board_id,{'version':0,'name':'blocked'}).status_code,503)
        self.assertEqual(self.client.get('/api/updates/progress').json['progress']['status'],'queued')

    def test_failed_interrupted_download_and_log(self):
        self.configure()
        raw=self.bundle()
        with patch('updater.github',return_value=[self.release(raw)]): self.request('POST','/api/updates/check',{})
        for failure in (ValueError('Rede interrompida'),None):
            with patch('updater.github',side_effect=failure,return_value=raw+b'corrupt'):
                self.assertEqual(self.request('POST','/api/updates/download',{}).status_code,400)
            self.assertFalse((self.directory/'download.json').exists())
        self.assertEqual(self.client.get('/api/updates/progress').json['progress']['status'],'failed')
        self.assertEqual(len(self.client.get('/api/activity?action=update.failed').json['entries']),2)
        with patch('updater.github',return_value=raw):
            self.assertEqual(self.request('POST','/api/updates/download',{}).status_code,200)

    def test_previews_filtered_and_untrusted_redirect(self):
        self.configure()
        raw=self.bundle()
        preview={**self.release(raw),'tag_name':'2.0.0-beta.1','prerelease':True}
        with patch('updater.github',return_value=[preview,self.release(raw)]):
            self.assertEqual(self.request('POST','/api/updates/check',{}).json['state']['release']['version'],TARGET)
        req=__import__('urllib.request',fromlist=['Request']).Request('https://api.github.com/x',headers={'Authorization':'Bearer secret'})
        with self.assertRaises(ValueError): updater.Redirect().redirect_request(req,None,302,'',{},'https://evil.test/a')
        redirected=updater.Redirect().redirect_request(req,None,302,'',{},'https://release-assets.githubusercontent.com/a')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_presotto_channel_filters_and_persists(self):
        result = self.request('PATCH', '/api/updates/settings', {
            'version': 0, 'owner': 'presotto', 'repository': 'kanban', 'channel': 'release-presotto'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.client.get('/api/updates').json['settings']['channel'], 'release-presotto')
        base = self.release(b'package')
        releases = [{**base, 'tag_name': tag, 'prerelease': True} for tag in (
            'v9.0.0', 'v9.0.0-beta.1', 'v9.0.0-release-presotto-other.1',
            'v2.0.0-release-presotto.2', 'v2.0.0-release-presotto.10')]
        releases.append({**base, 'tag_name': 'v3.0.0-release-presotto.1', 'draft': True})
        with patch('updater.github', return_value=releases):
            response = self.request('POST', '/api/updates/check', {})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['state']['release']['version'], '2.0.0-release-presotto.10')
        with patch('updater.github', return_value=releases[:3]):
            response = self.request('POST', '/api/updates/check', {})
        self.assertIsNone(response.json['state']['release'])

    def test_package_rejects_traversal_and_dependency_changes(self):
        raw=self.bundle()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files={name:archive.read(name) for name in archive.namelist()}
        for name,data in [('../escape.txt',b'x'),('data/kanban.sqlite3',b'x'),('requirements.txt',b'unsafe-package')]:
            modified=dict(files)
            modified[name]=data
            manifest=json.loads(modified['release.json']);manifest['files'][name]=hashlib.sha256(data).hexdigest()
            modified['release.json']=json.dumps(manifest).encode()
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as archive:
                for key,value in modified.items():archive.writestr(key,value)
            with self.assertRaises(ValueError):updater.unpack(stream.getvalue(),self.directory/'unsafe','1.0.1',Path('requirements.txt').read_bytes())
        self.assertFalse((self.directory/'unsafe').exists())

    def test_dependency_comparison_accepts_windows_line_endings_and_utf8_bom(self):
        raw=self.bundle()
        requirements=Path('requirements.txt').read_bytes()
        variants=[requirements.replace(b'\n',b'\r\n'),b'\xef\xbb\xbf'+requirements,requirements.rstrip(b'\n')]
        for index,value in enumerate(variants):
            destination=self.directory/('format-'+str(index))
            manifest=updater.unpack(raw,destination,CURRENT,value)
            self.assertEqual(manifest['version'],TARGET)
            self.assertEqual((destination/'requirements.txt').read_bytes(),requirements)
        with self.assertRaisesRegex(ValueError,'dependências'):
            updater.unpack(raw,self.directory/'changed',CURRENT,requirements+b'\nadditional-package==1.0\n')
        self.assertFalse((self.directory/'changed').exists())

    def test_windows_bridge_preserves_dependencies_and_migrations(self):
        self.bundle()
        source=Path(self.temp.name)/'source'
        output=Path(self.temp.name)/'windows-bridge.zip'
        build(source,output,windows_requirements=True)
        expected=(source/'requirements.txt').read_bytes().replace(b'\n',b'\r\n')
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('requirements.txt'),expected)
            for migration in (source/'migrations').glob('*.sql'):
                self.assertEqual(archive.read('migrations/'+migration.name),migration.read_bytes())
        updater.unpack(output.read_bytes(),self.directory/'bridge',CURRENT,expected)

    def test_lock_prevents_multiple_operations(self):
        self.configure()
        with updater.lock(self.directory):
            self.assertEqual(self.request('POST','/api/updates/check',{}).status_code,400)

    def test_delegated_status_independent_and_install_admin_only(self):
        account=self.create_user(role='Member',overrides={'view_update_status':True,'install_updates':True,'view_update_history':True})
        client,csrf=self.login_user()
        self.assertEqual(client.get('/api/updates').status_code,200)
        self.assertEqual(client.get('/api/updates/history').status_code,200)
        self.assertEqual(self.request('POST','/api/updates/install',{'confirm':True},client,csrf).status_code,403)
        self.assertEqual(self.request('POST','/api/updates/check',{},client,csrf).status_code,403)

    def test_rollback_rejects_different_database_schema(self):
        updater.atomic(self.directory/'previous.json',{'version':'0.9.0','schema':2,'path':'unused'})
        with patch.dict(os.environ,{'KANBAN_MANAGED':'1'}):
            result=self.request('POST','/api/updates/rollback',{'confirm':True,'version':'0.9.0'})
        self.assertEqual(result.status_code,400)
        self.assertIn('esquema',result.json['error'])
        self.assertFalse((self.directory/'job.json').exists())


if __name__=='__main__':unittest.main()
