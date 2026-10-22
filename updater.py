import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request
import zipfile
from pathlib import Path
from contextlib import contextmanager
from contextlib import closing

PERMISSIONS = {
    'view_update_status': 'Visualizar atualizações', 'check_updates': 'Verificar atualizações',
    'download_updates': 'Baixar atualizações', 'install_updates': 'Instalar atualizações',
    'manage_update_settings': 'Configurar atualizações', 'view_update_history': 'Visualizar histórico de atualizações',
}
LIMIT = 64 * 1024 * 1024
DEFAULT_REPOSITORY = {'owner': 'EchsDee', 'repository': 'producao-presotto', 'channel': 'stable', 'version': 0}


def github_token(directory):

    token = os.environ.get('KANBAN_GITHUB_TOKEN', '')
    if token:
        return token
    if read(Path(directory) / 'auth.json', {}).get('source') != 'github_cli':
        return ''
    import subprocess
    try:
        result = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                                capture_output=True, text=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Não foi possível ler a credencial do GitHub CLI. Autentique o usuário do serviço com gh auth login.') from None
    if result.returncode or not result.stdout.strip():
        raise ValueError('Credencial do GitHub CLI indisponível. Execute gh auth login com o usuário do serviço.')
    return result.stdout.strip()


def credential_status(directory):
    source = 'GitHub CLI' if read(Path(directory) / 'auth.json', {}).get('source') == 'github_cli' else 'KANBAN_GITHUB_TOKEN'
    try:
        return bool(github_token(directory)), source
    except ValueError:
        return False, source


def version(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?', value)
    if not match:
        raise ValueError('Versão semântica inválida.')
    major, minor, patch, pre = match.groups()
    identifiers = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split('.')) if pre else ()
    if pre and any(not p or (p.isdigit() and len(p) > 1 and p[0] == '0') for p in pre.split('.')):
        raise ValueError('Pré-versão inválida.')
    return int(major), int(minor), int(patch), not bool(pre), identifiers


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default


@contextmanager
def lock(directory, shared=False):
    path = Path(directory) / 'operation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            os.write(descriptor, b'0')
            os.lseek(descriptor, 0, 0)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield descriptor
    except (BlockingIOError, PermissionError):
        raise ValueError('Outra atualização está em andamento.') from None
    finally:
        os.close(descriptor)


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlparse
        parsed = urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in ('api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise ValueError('Redirecionamento de download não autorizado.')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if parsed.hostname != 'api.github.com':
            result.remove_header('Authorization')
        return result


def github(path, token='', binary=False):
    headers = {'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
               'User-Agent': 'Producao-Presotto-Updater', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with urllib.request.build_opener(Redirect()).open(urllib.request.Request('https://api.github.com' + path, headers=headers), timeout=40) as response:
            data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('O pacote excede 64 MB.')
        return data if binary else json.loads(data)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Não foi possível acessar o GitHub. Verifique repositório, credencial e limite de requisições.') from None


def allowed(name):
    return name in ('app.py', 'updater.py', 'VERSION', 'requirements.txt') or bool(re.fullmatch(r'migrations/\d{3}_[a-z_]+\.sql', name)) or bool(re.fullmatch(r'dist/[A-Za-z0-9_./-]+', name) and '..' not in name and '//' not in name)


def requirements_content(value):
    return value.removeprefix(b'\xef\xbb\xbf').replace(b'\r\n', b'\n').replace(b'\r', b'\n').rstrip(b'\n')


def wsgi_restart_file(root):
    """Only the deployment entry point outside the update allowlist is touched."""
    root = Path(root).resolve()
    if root.name != 'app':
        return None
    path = root.parent / 'app.wsgi'
    return path if path.is_file() and not path.is_symlink() else None


def replace_wsgi_files(source, root, names):
    from uuid import uuid4
    for name in names:
        if not allowed(name):
            raise ValueError('Arquivo fora do protocolo de atualização.')
        target = Path(root) / name
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != Path(root).parent):
            raise ValueError('Links simbólicos não são permitidos na instalação.')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.update-' + uuid4().hex)
        try:
            temporary.write_bytes((Path(source) / name).read_bytes())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def restore_wsgi(root, database, journal):
    old = Path(journal['old_path'])
    replace_wsgi_files(old, root, journal['old_files'])
    for name in set(journal['new_files']) - set(journal['old_files']):
        if allowed(name):
            (Path(root) / name).unlink(missing_ok=True)
    with closing(sqlite3.connect(journal['backup'])) as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)


def wsgi_recover(root, database):
    """Run before initialization; no external supervisor or background process."""
    if not wsgi_restart_file(root):
        return
    directory = Path(database).resolve().parent / 'updates'
    with lock(directory / 'wsgi-requests'):
        journal = read(directory / 'wsgi-journal.json')
        if journal and journal['phase'] == 'applying':
            restore_wsgi(root, database, journal)
            atomic(directory / 'progress.json', {'status': 'failed', 'percent': 100})
            (directory / 'wsgi-journal.json').unlink()
            wsgi_restart_file(root).touch()
            raise RuntimeError('Atualização interrompida recuperada. Recarregue o aplicativo.')


def wsgi_apply(root, database, target, rollback=False):
    """Caller must hold both the request gate and operation lock."""
    import importlib.util
    import shutil
    import sys
    from uuid import uuid4
    root, database = Path(root).resolve(), Path(database).resolve()
    directory = database.parent / 'updates'
    restart = wsgi_restart_file(root)
    if not restart or not os.access(restart, os.W_OK):
        raise ValueError('O arquivo app.wsgi precisa permitir o reinício pela aplicação.')
    if read(directory / 'wsgi-journal.json'):
        raise ValueError('Aguarde o reinício da atualização anterior.')
    source = Path(target['path']) if rollback else directory / 'staging'
    files = target['files']
    required = {'app.py', 'updater.py', 'VERSION', 'requirements.txt', 'dist/index.html', 'dist/app.js', 'dist/theme.css'}
    if not required <= set(files):
        raise ValueError('Pacote incompleto.')
    for name, digest in files.items():
        if not allowed(name) or (source / name).is_symlink() or hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Os arquivos preparados foram alterados. Baixe novamente.')
    if (source / 'VERSION').read_text().strip() != target['version']:
        raise ValueError('Versão preparada inconsistente.')
    if requirements_content((source / 'requirements.txt').read_bytes()) != requirements_content((root / 'requirements.txt').read_bytes()):
        raise ValueError('Dependências alteradas; instalação assistida necessária.')
    for name in ('app.py', 'updater.py'):
        compile((source / name).read_bytes(), str(source / name), 'exec')
    if not rollback:
        for migration in (root / 'migrations').glob('*.sql'):
            if not (source / 'migrations' / migration.name).is_file() or (source / 'migrations' / migration.name).read_bytes() != migration.read_bytes():
                raise ValueError('O pacote altera migrações existentes.')
    token = uuid4().hex
    old = directory / 'releases' / ('wsgi-' + token)
    old.mkdir(parents=True)
    old_files = {}
    paths = [root / name for name in ('app.py', 'updater.py', 'VERSION', 'requirements.txt')]
    for folder in ('dist', 'migrations'):
        paths.extend(p for p in (root / folder).rglob('*') if p.is_file())
    for path in paths:
        name = path.relative_to(root).as_posix()
        if allowed(name):
            destination = old / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            old_files[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    from backup import backup
    saved_db = backup(database, directory / ('before-wsgi-' + token + '.sqlite3'))
    trial = backup(saved_db, directory / ('trial-wsgi-' + token + '.sqlite3'))
    with closing(sqlite3.connect(database)) as connection:
        schema = connection.execute('PRAGMA user_version').fetchone()[0]
    # Import and initialize the candidate against an isolated database first.
    module_name = '_presotto_candidate_' + token
    spec = importlib.util.spec_from_file_location(module_name, source / 'app.py')
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = candidate
    import_path = list(sys.path)
    try:
        spec.loader.exec_module(candidate)
        probe = candidate.create_app({'TESTING': True, 'DATABASE': str(trial)})
        with probe.test_client() as client:
            response = client.get('/')
            if response.status_code != 200:
                raise ValueError('A nova versão não passou na verificação inicial.')
            response.close()
        journal = {'phase': 'applying', 'old_path': str(old), 'old_files': old_files,
                   'new_files': files, 'backup': str(saved_db), 'version': target['version']}
        atomic(directory / 'wsgi-journal.json', journal)
        try:
            atomic(directory / 'progress.json', {'status': 'installing', 'percent': 50})
            replace_wsgi_files(source, root, files)
            for name in set(old_files) - set(files):
                (root / name).unlink(missing_ok=True)
            candidate.initialize(str(database))
            atomic(directory / 'previous.json', {'path': str(old), 'files': old_files,
                   'version': (old / 'VERSION').read_text().strip(), 'schema': schema})
            journal['phase'] = 'restart_pending'
            atomic(directory / 'wsgi-journal.json', journal)
            history = read(directory / 'history.json', [])
            history.append({'from': (old / 'VERSION').read_text().strip(), 'to': target['version'],
                            'status': 'restart_pending', 'finished_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'message': 'Arquivos aplicados; reinício WSGI solicitado.'})
            atomic(directory / 'history.json', history[-100:])
            atomic(directory / 'progress.json', {'status': 'restart_pending', 'percent': 90})
            restart.touch()
            (directory / 'download.json').unlink(missing_ok=True)
        except Exception:
            restore_wsgi(root, database, journal)
            (directory / 'wsgi-journal.json').unlink(missing_ok=True)
            atomic(directory / 'progress.json', {'status': 'failed', 'percent': 100})
            restart.touch()
            raise
    finally:
        sys.path[:] = import_path
        sys.modules.pop(module_name, None)
        # Keep the database and code backups; the preflight copy is disposable.
        for suffix in ('', '-wal', '-shm'):
            Path(str(trial) + suffix).unlink(missing_ok=True)


def unpack(raw, destination, current, requirements):

    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 500 or sum(x.file_size for x in entries) > LIMIT:
            raise ValueError('Pacote muito grande.')
        names = [x.filename for x in entries]
        if len(set(names)) != len(names) or 'release.json' not in names:
            raise ValueError('Manifesto ausente ou arquivos duplicados.')
        manifest = json.loads(archive.read('release.json'))
        files = manifest.get('files', {})
        if manifest.get('protocol') != 1 or not isinstance(files, dict) or set(names) != set(files) | {'release.json'}:
            raise ValueError('Protocolo de atualização incompatível.')
        if version(current) < version(manifest['minimum_version']):
            raise ValueError('É necessário instalar uma versão intermediária.')
        version(manifest['version'])
        for required in ('app.py', 'updater.py', 'VERSION', 'requirements.txt', 'dist/index.html', 'dist/app.js', 'dist/theme.css'):
            if required not in files:
                raise ValueError('Pacote incompleto.')
        if requirements_content(archive.read('requirements.txt')) != requirements_content(requirements):
            raise ValueError('Esta versão altera dependências e exige atualização assistida pelo operador.')
        if archive.read('VERSION').decode().strip() != manifest['version']:
            raise ValueError('Versão do pacote inconsistente.')
        migrations = sorted(name for name in files if name.startswith('migrations/'))
        if not migrations or [int(name.split('/')[1].split('_')[0]) for name in migrations] != list(range(1, len(migrations) + 1)):
            raise ValueError('Sequência de migrações inválida.')
        for name, digest in files.items():
            if not allowed(name) or (archive.getinfo(name).external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Caminho não permitido no pacote.')
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Checksum interno inválido.')
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        for name in files:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
        return manifest


def register(app, need, body, audit):
    from flask import abort, g, jsonify, request
    root = Path(app.root_path).resolve()
    directory = Path(app.config['DATABASE']).resolve().parent / 'updates'
    installed = (root / 'VERSION').read_text().strip()
    directory.mkdir(parents=True, exist_ok=True)

    wsgi = wsgi_restart_file(root)
    if wsgi:
        journal = read(directory / 'wsgi-journal.json')
        if journal and journal['phase'] == 'restart_pending' and journal['version'] == installed:
            atomic(directory / 'progress.json', {'status': 'installed', 'percent': 100})
            history = read(directory / 'history.json', [])
            if history and history[-1]['to'] == installed:
                history[-1].update(status='installed', message='Nova versão carregada pela hospedagem WSGI.')
                atomic(directory / 'history.json', history)
            (directory / 'wsgi-journal.json').unlink()

        def wsgi_request_gate():
            gate = lock(directory / 'wsgi-requests', shared=True)
            try:
                g.wsgi_descriptor = gate.__enter__()
            except ValueError:
                abort(503, description='Atualização ou acesso em andamento. Tente novamente.')
            g.wsgi_gate = gate
            if (root / 'VERSION').read_text().strip() != installed or read(directory / 'wsgi-journal.json'):
                abort(503, description='Aguardando reinício WSGI. Recarregue em instantes.')

        # Acquire before authentication opens a database transaction.
        app.before_request_funcs.setdefault(None, []).insert(0, wsgi_request_gate)

        @app.teardown_request
        def release_wsgi_gate(error):
            gate = g.pop('wsgi_gate', None)
            if gate:
                gate.__exit__(None, None, None)

    def settings():
        row = g.db.execute("SELECT value FROM metadata WHERE key='updates'").fetchone()
        return json.loads(row[0]) if row else dict(DEFAULT_REPOSITORY)

    def save(value):
        g.db.execute("INSERT INTO metadata VALUES('updates',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),))

    def event(action, data):
        audit('update.' + action, 'application', 'updates', 'Produção Presotto', new=data)

    def admin(permission):
        need(permission)
        if g.user['role'] != 'Administrator':
            abort(403, description='A instalação e a configuração exigem um administrador.')

    def failure(error):
        abort(400, description=str(error))

    @app.get('/health')
    def update_health():
        import secrets
        expected = os.environ.get('KANBAN_HEALTH_KEY', '')
        if not expected or not secrets.compare_digest(request.headers.get('X-Update-Health', ''), expected):
            abort(404)
        with closing(sqlite3.connect(app.config['DATABASE'])) as connection:
            connection.execute('SELECT COUNT(*) FROM users').fetchone()
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                abort(503)
        return jsonify(version=installed)

    @app.post('/_update/stop')
    def update_stop():
        import secrets
        import threading
        expected = os.environ.get('KANBAN_HEALTH_KEY', '')
        if not expected or not secrets.compare_digest(request.headers.get('X-Update-Health', ''), expected):
            abort(404)
        threading.Timer(.2, lambda: os._exit(0)).start()
        return jsonify(ok=True)

    @app.before_request
    def maintenance():
        if request.path.startswith('/api/') and request.method not in ('GET', 'HEAD') and ((directory / 'job.json').exists() or (directory / 'journal.json').exists()):
            abort(503, description='Atualização em andamento. Aguarde a reinicialização; suas alterações não foram aplicadas.')

    @app.get('/api/updates')
    def status():
        need('view_update_status')
        value = settings()
        state = read(directory / 'state.json', {})
        managed = os.environ.get('KANBAN_MANAGED') == '1' or bool(wsgi)
        configured, source = credential_status(directory)
        return jsonify(current=installed, settings=value, state=state, managed=managed, install_mode='wsgi' if wsgi else 'launcher',
                       token_configured=configured, token_source=source,
                       downloaded=read(directory / 'download.json'), previous=read(directory / 'previous.json'))

    @app.patch('/api/updates/settings')
    def configure():
        admin('manage_update_settings')
        data = body()
        current = settings()
        if data.get('version') != current['version']:
            abort(409, description='Configuração alterada por outra sessão.')
        if set(data) != {'version', 'owner', 'repository', 'channel'} or not all(isinstance(data.get(k), str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', data[k]) and data[k] not in ('.', '..') for k in ('owner', 'repository')) or data.get('channel') not in ('stable', 'preview', 'release-presotto'):
            abort(400, description='Informe proprietário, repositório e canal válidos.')
        try:
            with lock(directory):
                if read(directory / 'job.json'):
                    raise ValueError('Aguarde a instalação em andamento.')
                current = {k: data[k] for k in ('owner', 'repository', 'channel') } | {'version': current['version'] + 1}
                save(current)
                for name in ('download.json', 'state.json'):
                    (directory / name).unlink(missing_ok=True)
                event('configured', current)
        except ValueError as error:
            failure(error)
        return jsonify(settings=current)

    @app.get('/api/updates/settings')
    def update_configuration():
        admin('manage_update_settings')
        configured, source = credential_status(directory)
        return jsonify(settings=settings(), token_configured=configured, token_source=source)

    @app.post('/api/updates/check')
    def check():
        need('check_updates')
        config = settings()
        if not config['owner']:
            abort(400, description='Configure o repositório primeiro.')
        try:
            with lock(directory):
                if read(directory / 'job.json'):
                    raise ValueError('Aguarde a instalação em andamento.')
                releases = github(f"/repos/{config['owner']}/{config['repository']}/releases?per_page=100", github_token(directory))
                candidates = []
                for release in releases:
                    try:
                        parsed = version(release['tag_name'])
                    except (ValueError, KeyError):
                        continue
                    if release.get('draft') or (config['channel'] == 'stable' and (release.get('prerelease') or not parsed[3])):
                        continue
                    if config['channel'] == 'release-presotto':
                        tag = release['tag_name'].removeprefix('v')
                        if not re.fullmatch(r'\d+\.\d+\.\d+-release-presotto(?:\.[0-9A-Za-z-]+)*(?:\+[0-9A-Za-z.-]+)?', tag):
                            continue
                    candidates.append((parsed, release))
                release = max(candidates, key=lambda x: x[0])[1] if candidates else None
                value = {'checked_at': time.time(), 'status': 'available' if release and version(release['tag_name']) > version(installed) else 'current', 'release': None}
                if release:
                    asset = next((a for a in release.get('assets', []) if a['name'] == 'presotto-update.zip' and re.fullmatch(r'sha256:[a-f0-9]{64}', a.get('digest') or '')), None)
                    value['release'] = {'version': release['tag_name'].removeprefix('v'), 'notes': (release.get('body') or '')[:20000], 'published_at': release.get('published_at'), 'asset_id': asset['id'] if asset else None, 'digest': asset['digest'] if asset else None, 'size': asset['size'] if asset else None, 'repository': config['owner'] + '/' + config['repository']}
                atomic(directory / 'state.json', value)
                event('checked', {'current': installed, 'latest': value['release']['version'] if release else None})
        except ValueError as error:
            event('failed', {'operation': 'check', 'current': installed, 'result': 'Falha na consulta ao GitHub.'})
            g.commit_error = True
            failure(error)
        return jsonify(state=value)

    @app.post('/api/updates/download')
    def download():
        need('download_updates')
        import shutil
        config = settings()
        try:
            with lock(directory):
                if read(directory / 'job.json'):
                    raise ValueError('Aguarde a instalação em andamento.')
                release = (read(directory / 'state.json', {}).get('release') or {})
                if not release.get('asset_id') or release.get('repository') != config['owner'] + '/' + config['repository'] or version(release['version']) <= version(installed):
                    raise ValueError('Verifique uma nova versão com pacote e SHA-256 publicados pelo GitHub.')
                atomic(directory / 'progress.json', {'status': 'downloading', 'percent': 0})
                try:
                    raw = github(f"/repos/{release['repository']}/releases/assets/{int(release['asset_id'])}", github_token(directory), True)
                    if len(raw) != release['size'] or 'sha256:' + hashlib.sha256(raw).hexdigest() != release['digest']:
                        raise ValueError('Checksum do download inválido.')
                    stage = directory / 'staging'
                    if stage.exists():
                        shutil.rmtree(stage)
                    manifest = unpack(raw, stage, installed, (root / 'requirements.txt').read_bytes())
                    if manifest['version'] != release['version']:
                        raise ValueError('Pacote não corresponde à versão publicada.')
                    for migration in (root / 'migrations').glob('*.sql'):
                        if not (stage / 'migrations' / migration.name).exists() or (stage / 'migrations' / migration.name).read_bytes() != migration.read_bytes():
                            raise ValueError('O pacote remove ou altera migrações já aplicadas.')
                    release['files'] = manifest['files']
                    atomic(directory / 'download.json', release)
                    atomic(directory / 'progress.json', {'status': 'downloaded', 'percent': 100})
                    event('downloaded', {'from': installed, 'to': release['version']})
                except Exception:
                    atomic(directory / 'progress.json', {'status': 'failed', 'percent': 0})
                    (directory / 'download.json').unlink(missing_ok=True)
                    raise
        except (ValueError, zipfile.BadZipFile, KeyError) as error:
            event('failed', {'operation': 'download', 'current': installed, 'result': 'Download ou validação do pacote falhou.'})
            g.commit_error = True
            failure(ValueError('Pacote inválido ou incompatível.') if not isinstance(error, ValueError) else error)
        return jsonify(ok=True)

    @app.get('/api/updates/progress')
    def progress():
        need('view_update_status')
        return jsonify(progress=read(directory / 'progress.json', {'status': 'idle', 'percent': 0}))

    @app.post('/api/updates/install')
    @app.post('/api/updates/rollback')
    def install():
        admin('install_updates')
        data = body()
        rollback = request.path.endswith('/rollback')
        if data.get('confirm') is not True:
            abort(400, description='Confirme a reinicialização antes de continuar.')
        if os.environ.get('KANBAN_MANAGED') != '1' and not wsgi:
            abort(409, description='Inicie o servidor com python launcher.py para instalar atualizações.')
        try:
            with lock(directory):
                if read(directory / 'job.json'):
                    raise ValueError('Outra atualização está em andamento.')
                target = read(directory / ('previous.json' if rollback else 'download.json'))
                if not target or data.get('version') != target['version']:
                    raise ValueError('Versão não disponível ou alterada. Atualize o painel.')
                if not rollback and target['repository'] != settings()['owner'] + '/' + settings()['repository']:
                    raise ValueError('O repositório de origem foi alterado.')
                if rollback and target['schema'] != g.db.execute('PRAGMA user_version').fetchone()[0]:
                    raise ValueError('Rollback bloqueado: esquema do banco incompatível. Use recuperação assistida para preservar os dados atuais.')
                event('rollback_requested' if rollback else 'install_requested', {'from': installed, 'to': target['version']})

                g.db.commit()
                if wsgi:
                    try:
                        if os.name != 'nt':
                            import fcntl
                            try:
                                fcntl.flock(g.wsgi_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                raise ValueError('Há acessos em andamento. Tente instalar novamente em instantes.')
                        wsgi_apply(root, app.config['DATABASE'], target, rollback)
                    except Exception as error:
                        app.logger.exception('WSGI update failed')
                        atomic(directory / 'progress.json', {'status': 'failed', 'percent': 100})
                        raise ValueError('A instalação WSGI falhou; consulte o log. Os backups foram preservados.') from error
                    return jsonify(ok=True, restarting=True), 202
                atomic(directory / 'progress.json', {'status': 'queued', 'percent': 0})
                atomic(directory / 'job.json', {'rollback': rollback, 'actor_id': g.user['id'], 'actor_name': g.user['username'], 'from': installed, 'to': target['version'], 'created_at': time.time()})
        except ValueError as error:
            failure(error)
        return jsonify(ok=True, restarting=True), 202

    @app.get('/api/updates/history')
    def history():
        need('view_update_history')
        return jsonify(entries=read(directory / 'history.json', [])[-100:][::-1])
