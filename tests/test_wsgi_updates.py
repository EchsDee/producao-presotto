import hashlib
import importlib.util
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_updates
import updater


class WsgiUpdates(unittest.TestCase):
    request = test_updates.UpdateTests.request
    configure = test_updates.UpdateTests.configure
    download = test_updates.UpdateTests.download
    bundle = test_updates.UpdateTests.bundle
    release = test_updates.UpdateTests.release
    directory = test_updates.UpdateTests.directory
    create_user = test_updates.UpdateTests.create_user
    login_user = test_updates.UpdateTests.login_user
    def setUp(self):
        test_updates.UpdateTests.setUp(self)
        self.root = Path(self.temp.name) / 'hosting' / 'app'
        self.root.mkdir(parents=True)
        project = Path(__file__).resolve().parents[1]
        for name in ('app.py', 'updater.py', 'requirements.txt', 'VERSION'):
            shutil.copyfile(project / name, self.root / name)
        for name in ('dist', 'migrations'):
            shutil.copytree(project / name, self.root / name)
        self.restart = self.root.parent / 'app.wsgi'
        self.restart.write_bytes(b'# existing WSGI and backup configuration\n')
        (self.root / 'venv').mkdir()
        (self.root / 'venv' / 'keep').write_bytes(b'untouched')
        self.reload_wsgi()

    def reload_wsgi(self):
        cookie = self.client.get_cookie('kanban_session').value
        spec = importlib.util.spec_from_file_location('_wsgi_test_app', self.root / 'app.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        paths = list(sys.path)
        try:
            spec.loader.exec_module(module)
            self.app = module.create_app(self.config)
        finally:
            sys.path[:] = paths
        self.client = self.app.test_client()
        self.client.set_cookie('kanban_session', cookie)

    def tearDown(self):
        sys.modules.pop('_wsgi_test_app', None)
        test_updates.UpdateTests.tearDown(self)

    def test_wsgi_install_reload_and_rollback(self):
        self.configure()
        self.download(self.bundle())
        before = self.restart.read_bytes()
        timestamp = self.restart.stat().st_mtime_ns
        response = self.request('POST', '/api/updates/install', {'confirm': True, 'version': test_updates.TARGET})
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual((self.root / 'VERSION').read_text(), test_updates.TARGET)
        self.assertEqual(self.restart.read_bytes(), before)
        self.assertGreaterEqual(self.restart.stat().st_mtime_ns, timestamp)
        self.assertEqual((self.root / 'venv/keep').read_bytes(), b'untouched')
        self.assertTrue(Path(self.path).is_file())
        self.assertEqual(self.client.get('/api/boards').status_code, 503)
        self.reload_wsgi()
        result = self.client.get('/api/updates').json
        self.assertTrue(result['managed'])
        self.assertEqual(result['install_mode'], 'wsgi')
        self.assertEqual(self.client.get('/api/updates/progress').json['progress']['status'], 'installed')
        response = self.request('POST', '/api/updates/rollback', {'confirm': True, 'version': test_updates.CURRENT})
        self.assertEqual(response.status_code, 202, response.json)
        self.reload_wsgi()
        self.assertEqual(self.client.get('/api/updates').json['current'], test_updates.CURRENT)
        self.assertEqual(self.client.get('/api/boards').status_code, 200)

    def test_wsgi_rejects_tampering_and_broken_candidate(self):
        self.configure()
        self.download(self.bundle())
        (self.directory / 'staging/app.py').write_text('invalid syntax !!!')
        response = self.request('POST', '/api/updates/install', {'confirm': True, 'version': test_updates.TARGET})
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), test_updates.CURRENT)
        self.assertEqual(self.client.get('/api/boards').status_code, 200)
        self.download(self.bundle(broken=True))
        response = self.request('POST', '/api/updates/install', {'confirm': True, 'version': test_updates.TARGET})
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), test_updates.CURRENT)

    def test_wsgi_failed_replacement_recovers_code_and_database(self):
        self.configure()
        self.download(self.bundle())
        original = updater.replace_wsgi_files
        calls = []

        def fail_once(source, root, names):
            calls.append(True)
            if len(calls) == 1:
                original(source, root, ['VERSION'])
                raise OSError('simulated failure')
            return original(source, root, names)

        with patch('updater.replace_wsgi_files', side_effect=fail_once):
            response = self.request('POST', '/api/updates/install', {'confirm': True, 'version': test_updates.TARGET})
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.directory / 'wsgi-journal.json').exists())
        self.assertEqual((self.root / 'VERSION').read_text().strip(), test_updates.CURRENT)
        self.assertEqual(self.client.get('/api/boards').status_code, 200)

    def test_subpath_assets_and_api(self):
        response = self.client.get('/', environ_overrides={'SCRIPT_NAME': '/app'})
        self.assertIn(b'src="app.js"', response.data)
        self.assertIn(b'href="theme.css"', response.data)
        self.assertNotIn(b'src="/', response.data)
        response.close()
        script = (self.root / 'dist/app.js').read_text(encoding='utf-8')
        self.assertIn("fetch('api' + path", script)
        self.assertNotIn("fetch('/", script)

    def test_bootstrap_preserves_wsgi_requirements_and_environment(self):
        script = Path(__file__).resolve().parents[1] / 'packaging/enable_wsgi_updates.py'
        spec = importlib.util.spec_from_file_location('_bootstrap_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        archive = Path(self.temp.name) / 'bootstrap.zip'
        raw = self.bundle()
        archive.write_bytes(raw)
        before = self.restart.read_bytes()
        requirements = (self.root / 'requirements.txt').read_bytes()
        with self.assertRaises(ValueError):
            module.enable(self.root, archive, '0' * 64)
        module.enable(self.root, archive, hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.restart.read_bytes(), before)
        self.assertEqual((self.root / 'requirements.txt').read_bytes(), requirements)
        self.assertEqual((self.root / 'venv/keep').read_bytes(), b'untouched')

    def test_wsgi_permissions_and_inflight_request_gate(self):
        self.create_user(role='Member', overrides={'install_updates': True})
        client, csrf = self.login_user()
        response = self.request('POST', '/api/updates/install',
                                {'confirm': True, 'version': test_updates.TARGET}, client, csrf)
        self.assertEqual(response.status_code, 403)
        with updater.lock(self.directory / 'wsgi-requests'):
            self.assertEqual(self.client.get('/api/boards').status_code, 503)

    def test_interrupted_update_recovers_on_initialization(self):
        self.configure()
        self.download(self.bundle())
        replace = updater.replace_wsgi_files

        def interrupted(source, root, names):
            replace(source, root, ['VERSION'])
            raise KeyboardInterrupt()

        with patch('updater.replace_wsgi_files', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.request('POST', '/api/updates/install', {'confirm': True, 'version': test_updates.TARGET})
        self.assertTrue((self.directory / 'wsgi-journal.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'interrompida'):
            updater.wsgi_recover(self.root, self.path)
        self.assertEqual((self.root / 'VERSION').read_text().strip(), test_updates.CURRENT)
        self.assertFalse((self.directory / 'wsgi-journal.json').exists())


if __name__ == '__main__':
    unittest.main()
