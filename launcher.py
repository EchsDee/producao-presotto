"""Stable supervisor. Run this file for managed updates; it never runs package scripts."""
import hashlib
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from contextlib import closing

from updater import atomic, read, lock, allowed

ROOT = Path(__file__).resolve().parent


def process_identity(pid, terminate_if=None):
    """PID plus OS creation time prevents killing an unrelated reused PID."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        handle = kernel.OpenProcess(0x1000 | (1 if terminate_if else 0), False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(x) for x in times)):
                raise RuntimeError('Não foi possível verificar o processo do servidor.')
            identity = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
            if terminate_if == identity and not kernel.TerminateProcess(handle, 0):
                raise RuntimeError('Não foi possível parar o servidor anterior.')
            return identity
        finally:
            kernel.CloseHandle(handle)
    try:
        identity = subprocess.check_output(['ps', '-o', 'lstart=', '-p', str(pid)], text=True).strip()
    except subprocess.CalledProcessError:
        return None
    if terminate_if == identity:
        os.kill(pid, signal.SIGTERM)
    return identity


def snapshot(source, destination):
    destination.mkdir(parents=True, exist_ok=False)
    paths = [source / name for name in ('app.py', 'updater.py', 'VERSION', 'requirements.txt')]
    for name in ('dist', 'migrations'):
        paths.extend(p for p in (source / name).rglob('*') if p.is_file())
    for path in paths:
        name = path.relative_to(source).as_posix()
        if allowed(name):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def database_backup(source, target):
    with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy)


def record(directory, database, job, status, message):
    item = {**job, 'status': status, 'message': message, 'finished_at': datetime.now(timezone.utc).isoformat()}
    history = read(directory / 'history.json', [])
    history.append(item)
    atomic(directory / 'history.json', history)
    with closing(sqlite3.connect(database, timeout=20)) as db, db:
        db.execute('INSERT INTO activity_log(occurred_at,actor_id,actor_name,action,entity_type,entity_id,entity_name,previous_values,new_values) VALUES(?,?,?,?,?,?,?,?,?)',
                   (item['finished_at'], job['actor_id'], job['actor_name'], 'update.' + status, 'application', 'updates', 'Produção Presotto', json.dumps({'version': job['from']}), json.dumps({'version': job['to'], 'result': message})))


def supervise():
    database = Path(os.environ.get('KANBAN_DATABASE', ROOT / 'data' / 'kanban.sqlite3')).resolve()
    directory = database.parent / 'updates'
    directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    secret = read(directory / 'health-key.json')
    if not secret:
        secret = secrets.token_urlsafe(48)
        atomic(directory / 'health-key.json', secret)
        os.chmod(directory / 'health-key.json', 0o600)
    env.update(KANBAN_DATABASE=str(database), KANBAN_MANAGED='1', KANBAN_HEALTH_KEY=secret)
    host = env.get('KANBAN_HOST', '127.0.0.1')
    host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    base = f"http://{host}:{int(env.get('KANBAN_PORT', '8000'))}"
    child = None
    log = (directory / 'server.log').open('ab', buffering=0)

    def call(path, timeout=2):
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={'X-Update-Health': secret}, method='POST' if path.endswith('/stop') else 'GET'), timeout=timeout) as response:
            return json.load(response)

    def stop():
        nonlocal child
        if child and child.poll() is None:
            # Also stops the actual interpreter behind Windows virtualenv redirectors.
            try:
                call('/_update/stop')
                child.wait(timeout=3)
            except Exception:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child = None
        (directory / 'child.json').unlink(missing_ok=True)

    def start(code):
        nonlocal child
        child = subprocess.Popen([sys.executable, str(code / 'app.py')], cwd=code, env=env, stdout=log, stderr=log)
        atomic(directory / 'child.json', {'pid': child.pid, 'identity': process_identity(child.pid)})
        expected = (code / 'VERSION').read_text().strip()
        for _ in range(120):
            if child.poll() is not None:
                break
            try:
                if call('/health')['version'] == expected:
                    return
            except Exception:
                pass
            time.sleep(.25)
        stop()
        raise RuntimeError('A nova versão não passou na verificação de saúde.')

    def restore(journal):
        # Failed migrations cannot leave a newer schema under the previous code.
        backup = Path(journal['backup'])
        if backup.exists():
            database_backup(database, directory / ('failed-' + uuid4().hex + '.sqlite3'))
            database_backup(backup, database)
        atomic(directory / 'active.json', {'path': journal['old_path']})

    with lock(directory / 'supervisor'):
        # A previous supervisor may have been interrupted. Stop only its authenticated child.
        previous_child = read(directory / 'child.json')
        if previous_child and previous_child['identity']:
            process_identity(previous_child['pid'], terminate_if=previous_child['identity'])
            time.sleep(.5)
        try:
            call('/_update/stop')
            time.sleep(1)
        except Exception:
            pass
        if read(directory / 'journal.json'):
            journal = read(directory / 'journal.json')
            with lock(directory):
                restore(journal)
                start(Path(journal['old_path']))
                record(directory, database, journal['job'], 'failed', 'Instalação interrompida; versão e banco anteriores recuperados.')
                (directory / 'journal.json').unlink()
                (directory / 'job.json').unlink(missing_ok=True)
                atomic(directory / 'progress.json', {'status': 'failed', 'percent': 100})
        else:
            start(Path(read(directory / 'active.json', {'path': str(ROOT)})['path']))
        print(f'Produção Presotto: {base} (atualizações habilitadas)', flush=True)
        try:
            while True:
                job = read(directory / 'job.json')
                if job:
                    # Let the HTTP response and activity transaction finish first.
                    time.sleep(2)
                    with lock(directory):
                        active = Path(read(directory / 'active.json', {'path': str(ROOT)})['path'])
                        old = directory / 'releases' / uuid4().hex
                        old.parent.mkdir(parents=True, exist_ok=True)
                        snapshot(active, old)
                        target = read(directory / ('previous.json' if job['rollback'] else 'download.json'))
                        with closing(sqlite3.connect(database)) as db:
                            actor = db.execute('SELECT role,enabled FROM users WHERE id=?', (job['actor_id'],)).fetchone()
                        if not actor or actor != ('Administrator', 1) or not target or target['version'] != job['to']:
                            record(directory, database, job, 'failed', 'Autorização ou pacote não está mais disponível.')
                            (directory / 'job.json').unlink()
                            continue
                        code = Path(target['path']) if job['rollback'] else directory / 'staging'
                        if not job['rollback']:
                            invalid = False
                            for name, digest in target['files'].items():
                                if not allowed(name) or not (code / name).is_file() or hashlib.sha256((code / name).read_bytes()).hexdigest() != digest:
                                    invalid = True
                                    break
                            if invalid:
                                record(directory, database, job, 'failed', 'Os arquivos preparados foram alterados. Baixe novamente a versão.')
                                (directory / 'job.json').unlink()
                                (directory / 'download.json').unlink(missing_ok=True)
                                atomic(directory / 'progress.json', {'status': 'failed', 'percent': 0})
                                continue
                            release = directory / 'releases' / uuid4().hex
                            snapshot(code, release)
                            code = release
                        stop()
                        backup = directory / ('before-' + uuid4().hex + '.sqlite3')
                        database_backup(database, backup)
                        with closing(sqlite3.connect(database)) as db:
                            schema = db.execute('PRAGMA user_version').fetchone()[0]
                        journal = {'old_path': str(old), 'backup': str(backup), 'job': job}
                        # The journal is durable before any new code or migration is run.
                        atomic(directory / 'journal.json', journal)
                        if (ROOT / '.env').exists():
                            shutil.copyfile(ROOT / '.env', backup.with_suffix('.env'))
                            os.chmod(backup.with_suffix('.env'), 0o600)
                        atomic(directory / 'progress.json', {'status': 'installing', 'percent': 50})
                        try:
                            start(code)
                            atomic(directory / 'active.json', {'path': str(code)})
                            atomic(directory / 'previous.json', {'path': str(old), 'version': job['from'], 'schema': schema})
                            record(directory, database, job, 'rolled_back' if job['rollback'] else 'installed', 'Versão iniciada e verificada. Dados preservados.')
                            atomic(directory / 'progress.json', {'status': 'installed', 'percent': 100})
                        except Exception:
                            stop()
                            restore(journal)
                            start(old)
                            record(directory, database, job, 'failed', 'Falha na inicialização. Código e banco anteriores recuperados.')
                            atomic(directory / 'progress.json', {'status': 'failed', 'percent': 100})
                        (directory / 'journal.json').unlink()
                        (directory / 'job.json').unlink()
                        (directory / 'download.json').unlink(missing_ok=True)
                if child and child.poll() is not None:
                    raise RuntimeError('Servidor encerrado. Consulte data/updates/server.log.')
                time.sleep(.5)
        finally:
            stop()
            log.close()


if __name__ == '__main__':
    try:
        supervise()
    except KeyboardInterrupt:
        pass
