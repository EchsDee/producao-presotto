from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import warnings
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from contextlib import closing

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
sys.path.insert(0, str(Path(__file__).resolve().parent))
import updater

ROOT = Path(__file__).resolve().parent
PERMISSIONS = {
    'view_boards': 'Visualizar quadros',
    'manage_boards': 'Criar, renomear e excluir quadros',
    'create_tasks': 'Criar tarefas', 'edit_tasks': 'Editar detalhes das tarefas',
    'move_tasks': 'Mover e reordenar tarefas', 'delete_tasks': 'Excluir tarefas',
    'manage_columns': 'Criar, editar e reordenar colunas', 'delete_columns': 'Excluir colunas',
    'upload_attachments': 'Enviar arquivos', 'download_attachments': 'Visualizar e baixar arquivos',
    'delete_attachments': 'Excluir quaisquer arquivos', 'delete_own_attachments': 'Excluir os próprios arquivos',
    'assign_users': 'Atribuir usuários às tarefas', 'assign_self': 'Atribuir tarefas a si mesmo',
    'view_task_assignees': 'Visualizar responsáveis',
    'view_activity_log': 'Visualizar registro de atividades', 'view_task_history': 'Visualizar histórico das tarefas',
    'manage_users': 'Criar, editar, desativar e excluir usuários; redefinir senhas',
    'manage_permissions': 'Definir perfis e configurar permissões individuais',
    'view_tags': 'Visualizar tags', 'assign_tags': 'Adicionar tags às tarefas',
    'remove_tags': 'Remover tags das tarefas', 'create_tags': 'Cadastrar tags',
    'edit_tags': 'Editar tags', 'delete_tags': 'Excluir tags',
    'manage_tag_status': 'Ativar e desativar tags',
}
PERMISSIONS.update(updater.PERMISSIONS)
ROLES = {
    'Administrator': list(PERMISSIONS),
    'Manager': ['view_boards', 'create_tasks', 'edit_tasks', 'move_tasks', 'delete_tasks',
                'manage_columns', 'delete_columns', 'upload_attachments', 'download_attachments',
                'delete_attachments', 'delete_own_attachments', 'assign_users', 'assign_self',
                'view_task_assignees', 'view_activity_log', 'view_task_history', 'view_tags', 'assign_tags', 'remove_tags'],
    'Member': ['view_boards', 'create_tasks', 'edit_tasks', 'move_tasks', 'upload_attachments',
               'download_attachments', 'delete_own_attachments', 'assign_self',
               'view_task_assignees', 'view_task_history', 'view_tags', 'assign_tags', 'remove_tags'],
    'Viewer': ['view_boards', 'download_attachments', 'view_task_assignees', 'view_task_history', 'view_tags'],
}
ACTIVITY_ACTIONS = {
    'tag.created': 'Tag cadastrada', 'tag.updated': 'Tag alterada', 'tag.deleted': 'Tag excluída',
    'tag.activated': 'Tag ativada', 'tag.deactivated': 'Tag desativada',
    'task.tag_added': 'Tag adicionada à tarefa', 'task.tag_removed': 'Tag removida da tarefa',
    **{'update.' + key: value for key, value in {
        'configured': 'Atualizações configuradas', 'checked': 'Atualizações verificadas',
        'downloaded': 'Atualização baixada', 'install_requested': 'Instalação autorizada',
        'rollback_requested': 'Restauração autorizada', 'installed': 'Atualização instalada',
        'failed': 'Falha na atualização', 'rolled_back': 'Versão anterior restaurada',
    }.items()},
    'user.profile_updated': 'Nome de exibição alterado', 'user.avatar_changed': 'Foto de perfil alterada',
    'user.password_changed': 'Senha alterada', 'user.password_reset': 'Senha redefinida',
    'settings.theme_changed': 'Cores do sistema alteradas',
    'task.created': 'Tarefa criada', 'task.updated': 'Tarefa editada', 'task.deleted': 'Tarefa excluída',
    'task.moved': 'Tarefa movida ou reordenada', 'task.assigned': 'Responsável atribuído', 'task.unassigned': 'Responsável removido',
    'column.created': 'Coluna criada', 'column.updated': 'Coluna renomeada ou alterada',
    'column.deleted': 'Coluna excluída', 'column.reordered': 'Coluna reordenada',
    'attachment.uploaded': 'Arquivo enviado', 'attachment.deleted': 'Arquivo excluído',
    'user.created': 'Conta criada', 'user.updated': 'Conta alterada', 'user.deleted': 'Conta excluída',
    'user.role_changed': 'Perfil alterado', 'user.permissions_changed': 'Permissões alteradas',
    'board.created': 'Quadro criado', 'board.updated': 'Quadro alterado', 'board.deleted': 'Quadro excluído',
}
PH = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=65536, parallelism=4)

DUMMY_HASH = PH.hash(secrets.token_urlsafe(32))


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid4())


def effective(user):
    if user['role'] == 'Administrator':
        return list(PERMISSIONS)
    result = set(ROLES[user['role']])
    for name, allowed in json.loads(user['permissions']).items():
        if allowed:
            result.add(name)
        else:
            result.discard(name)
    return sorted(result)


def public_user(user, admin=False):
    value = {k: user[k] for k in ('id', 'username', 'role', 'version')}
    value.update(enabled=bool(user['enabled']), must_change_password=bool(user['must_change_password']),
                 permissions=effective(user), **profile_identity(user), profile_version=user['profile_version'])
    if admin:
        value['overrides'] = json.loads(user['permissions'])
    return value


def profile_identity(user, avatar_allowed=True):
    return {'display_name': user['display_name'] or user['username'],
            'avatar_url': f"/api/users/{user['id']}/avatar?v={user['avatar_version']}"
            if user['avatar'] and avatar_allowed else None}


def connect(path):
    db = sqlite3.connect(path, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=20000')
    db.execute('PRAGMA synchronous=FULL')
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        migrations = sorted((ROOT / 'migrations').glob('*.sql'))
        if version > len(migrations):
            raise RuntimeError('Database is newer than this application; use the matching application version.')
        for migration in migrations[version:]:


            for statement in migration.read_text(encoding='utf-8').split(';'):
                if statement.strip():
                    db.execute(statement)
            if migration.name.startswith('002_'):


                for user in db.execute('SELECT id,permissions FROM users').fetchall():
                    overrides = json.loads(user['permissions'])
                    for old, new in [('view_boards', 'download_attachments'),
                                     ('edit_tasks', 'assign_users'), ('edit_tasks', 'assign_self'),
                                     ('delete_attachments', 'delete_own_attachments')]:
                        if old in overrides:
                            overrides.setdefault(new, overrides[old])
                    if overrides != json.loads(user['permissions']):
                        db.execute('UPDATE users SET permissions=?,version=version+1 WHERE id=?',
                                   (json.dumps(overrides), user['id']))
            if migration.name.startswith('006_'):
                for task in db.execute('SELECT id,tags,created_at,created_by FROM tasks ORDER BY created_at,id').fetchall():
                    for name in json.loads(task['tags']):
                        name = name.strip()
                        key = name.casefold()
                        tag = db.execute('SELECT id FROM tags WHERE name_key=?', (key,)).fetchone()
                        tag_id = tag['id'] if tag else uid()
                        if not tag:
                            creator = db.execute('SELECT username FROM users WHERE id=?', (task['created_by'],)).fetchone()
                            db.execute('INSERT INTO tags(id,name,name_key,created_by,creator_name,created_at) VALUES(?,?,?,?,?,?)',
                                       (tag_id, name, key, task['created_by'], creator[0] if creator else 'Usuário excluído', task['created_at']))
                        db.execute('INSERT OR IGNORE INTO task_tags VALUES(?,?)', (task['id'], tag_id))
            db.execute(f'PRAGMA user_version={int(migration.name.split("_")[0])}')
        if not db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
            admin_id = uid()
            db.execute('INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                       (admin_id, 'Admin', PH.hash('adminpasspres'), 'Administrator', now()))
            board_id = uid()
            db.execute('INSERT INTO boards(id,name,position,created_by,created_at) VALUES(?,?,?,?,?)',
                       (board_id, 'Meu quadro', 0, admin_id, now()))
            for pos, (name, kind) in enumerate([('A fazer', 'todo'), ('Em andamento', 'doing'), ('Concluído', 'done')]):
                db.execute('INSERT INTO columns VALUES(?,?,?,?,?)', (uid(), board_id, name, kind, pos))
            db.execute("INSERT INTO metadata VALUES('initialized',?)", (now(),))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DATABASE=os.environ.get('KANBAN_DATABASE', str(ROOT / 'data' / 'kanban.sqlite3')),
        MAX_FILE_BYTES=int(os.environ.get('KANBAN_MAX_FILE_MB', '20')) * 1024 * 1024,
        SESSION_HOURS=int(os.environ.get('KANBAN_SESSION_HOURS', '12')),
        COOKIE_SECURE=os.environ.get('KANBAN_COOKIE_SECURE', '0') == '1',
        PUBLIC_ORIGIN=os.environ.get('KANBAN_PUBLIC_ORIGIN', ''),
        MAX_FORM_MEMORY_SIZE=128 * 1024, MAX_FORM_PARTS=20,
        MAX_AVATAR_BYTES=5 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    app.config['MAX_CONTENT_LENGTH'] = max(app.config['MAX_FILE_BYTES'], app.config['MAX_AVATAR_BYTES']) + 1024 * 1024
    initialize(app.config['DATABASE'])

    def db():
        return g.db

    def need(permission):
        if permission not in effective(g.user):
            abort(403, description='Você não tem permissão para realizar esta ação.')

    def body():
        if not request.is_json:
            abort(415, description='Envie uma solicitação JSON.')
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400, description='A solicitação deve conter um objeto JSON.')
        return data

    def text(data, name, maximum, required=True):
        value = data.get(name, '')
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            abort(400, description=f"Campo { {'title':'título','description':'descrição','name':'nome','username':'usuário'}.get(name,name) } inválido; limite de {maximum} caracteres.")
        return value.strip() if required else value

    def password(data, name='password'):
        value = data.get(name)
        if not isinstance(value, str) or not 10 <= len(value) <= 256:
            abort(400, description='Use uma senha de 10 a 256 caracteres.')
        return value

    def lookup(table, item_id):
        row = db().execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
        if not row:
            abort(404, description='Este item não existe mais.')
        return row

    def expected(data, row):
        version = data.get('version')
        if type(version) is not int:
            abort(400, description='É necessário informar a versão do item.')
        if version != row['version']:
            abort(409, description='Outra pessoa alterou este item. Atualize e revise suas alterações antes de salvar novamente.')

    def board_write(board_id, data):
        board = lookup('boards', board_id)
        expected(data, board)
        return board

    def touch(board_id):
        db().execute('UPDATE boards SET version=version+1 WHERE id=?', (board_id,))

    def column_for(board_id, column_id):
        col = lookup('columns', column_id)
        if col['board_id'] != board_id:
            abort(400, description='Esta coluna pertence a outro quadro.')
        return col

    def audit(action, entity_type, entity_id, entity_name, *, board_id=None, task=None,
              previous=None, new=None):


        board = lookup('boards', board_id) if board_id else None
        actor = lookup('users', g.user['id'])
        if entity_type == 'user':
            subject = db().execute('SELECT display_name,username FROM users WHERE id=?', (entity_id,)).fetchone()
            if subject:
                entity_name = subject['display_name'] or subject['username']
        db().execute('INSERT INTO activity_log(occurred_at,actor_id,actor_name,action,entity_type,'
                     'entity_id,entity_name,board_id,board_name,task_id,task_name,previous_values,new_values) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                         now(), g.user['id'], actor['display_name'] or actor['username'], action, entity_type, entity_id, entity_name,
                         board_id, board['name'] if board else None, task['id'] if task else None,
                         task['title'] if task else None, json.dumps(previous or {}), json.dumps(new or {})))

    def task_snapshot(task):
        col = lookup('columns', task['column_id'])
        return {**{k: task[k] for k in ('title', 'description', 'priority', 'column_id', 'position', 'task_date', 'desired_delivery')}, 'tags': [t['name'] for t in registered_task_tags(task['id'])],
                'column_name': col['name']}

    def file_snapshot(attachment):
        uploader = db().execute("SELECT COALESCE(NULLIF(display_name,''),username) FROM users WHERE id=?", (attachment['uploaded_by'],)).fetchone()
        return {**{k: attachment[k] for k in ('filename', 'format', 'size', 'sha256', 'uploaded_by', 'uploaded_at')},
                'uploader': uploader[0] if uploader else 'Deleted user'}

    def require_file_deletion(attachment):
        permissions = effective(g.user)
        if 'delete_attachments' not in permissions and not (
                'delete_own_attachments' in permissions and attachment['uploaded_by'] == g.user['id']):
            abort(403, description='Você não tem permissão para excluir este arquivo nem a tarefa ou o quadro que o contém.')

    def log_task_deletion(task):
        for attachment in db().execute('SELECT id,filename,format,size,sha256,uploaded_by,uploaded_at '
                                       'FROM attachments WHERE task_id=?', (task['id'],)).fetchall():
            require_file_deletion(attachment)
            audit('attachment.deleted', 'attachment', attachment['id'], attachment['filename'],
                  board_id=task['board_id'], task=task, previous=file_snapshot(attachment))
        for account in db().execute('SELECT u.id,u.username,u.display_name FROM users u JOIN task_assignees a '
                                     'ON a.user_id=u.id WHERE a.task_id=?', (task['id'],)).fetchall():
            audit('task.unassigned', 'task', task['id'], task['title'], board_id=task['board_id'], task=task,
                  previous={'user_id': account['id'], 'username': account['username'],
                            'display_name': account['display_name'] or account['username'], 'reason': 'task_deleted'})
        audit('task.deleted', 'task', task['id'], task['title'], board_id=task['board_id'],
              task=task, previous=task_snapshot(task))

    def assign(task_id, data):
        if 'assignees' not in data:
            return
        ids = data['assignees']
        if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) for i in ids):
            abort(400, description='Os responsáveis informados são inválidos.')
        current = {r[0] for r in db().execute('SELECT user_id FROM task_assignees WHERE task_id=?', (task_id,))}
        desired = set(ids)
        changed = current ^ desired
        permissions = effective(g.user)
        if changed and 'assign_users' not in permissions:
            if 'assign_self' not in permissions or changed != {g.user['id']}:
                abort(403, description='Você não tem permissão para alterar estes responsáveis.')
        for user_id in desired:
            lookup('users', user_id)
        task = lookup('tasks', task_id)
        for user_id in sorted(current - desired):
            account = lookup('users', user_id)
            db().execute('DELETE FROM task_assignees WHERE task_id=? AND user_id=?', (task_id, user_id))
            audit('task.unassigned', 'task', task_id, task['title'], board_id=task['board_id'], task=task,
                  previous={'user_id': user_id, 'username': account['username'], 'display_name': account['display_name'] or account['username']})
        for user_id in sorted(desired - current):
            account = lookup('users', user_id)
            db().execute('INSERT INTO task_assignees VALUES(?,?)', (task_id, user_id))
            audit('task.assigned', 'task', task_id, task['title'], board_id=task['board_id'], task=task,
                  new={'user_id': user_id, 'username': account['username'], 'display_name': account['display_name'] or account['username']})

    def serialize_board(board_id):
        board = dict(lookup('boards', board_id))
        board['columns'] = [dict(c) for c in db().execute(
            'SELECT * FROM columns WHERE board_id=? ORDER BY position,id', (board_id,))]
        tasks = [dict(t) for t in db().execute(
            'SELECT * FROM tasks WHERE board_id=? ORDER BY position,id', (board_id,))]
        for task in tasks:
            details = registered_task_tags(task['id']) if 'view_tags' in effective(g.user) else []
            task['tag_details'] = details
            task['tag_ids'] = [t['id'] for t in details]
            task['tags'] = [t['name'] for t in details]
            assignees = [r[0] for r in db().execute(
                'SELECT user_id FROM task_assignees WHERE task_id=? ORDER BY user_id', (task['id'],))]
            task['assigned_to_me'] = g.user['id'] in assignees
            task['assignees'] = assignees if 'view_task_assignees' in effective(g.user) else []
            task['attachments'] = [dict(a) for a in db().execute(
                "SELECT a.id,a.filename,a.format,a.size,a.sha256,a.uploaded_at,a.uploaded_by,COALESCE(NULLIF(u.display_name,''),u.username) AS uploader "
                'FROM attachments a LEFT JOIN users u ON u.id=a.uploaded_by WHERE task_id=? ORDER BY a.uploaded_at,a.id',
                (task['id'],))] if 'download_attachments' in effective(g.user) else []
        board['tasks'] = tasks
        return board

    @app.before_request
    def authenticate():
        if not request.path.startswith('/api/'):
            return
        g.db = connect(app.config['DATABASE'])


        g.db.execute('BEGIN' if request.method in ('GET', 'HEAD') else 'BEGIN IMMEDIATE')
        g.user = None
        token = request.cookies.get('kanban_session', '')
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        g.auth_session = g.db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?',
                                     (token_hash, int(time.time()))).fetchone()
        if g.auth_session:
            g.user = g.db.execute('SELECT * FROM users WHERE id=? AND enabled=1',
                                  (g.auth_session['user_id'],)).fetchone()
        if request.method not in ('GET', 'HEAD'):
            origin = request.headers.get('Origin')
            allowed_origin = app.config['PUBLIC_ORIGIN'] or request.host_url
            if origin and origin.rstrip('/') != allowed_origin.rstrip('/'):
                abort(403, description='Solicitações de outra origem não são permitidas.')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                abort(403, description='Solicitações de outro site não são permitidas.')
        if request.path == '/api/login':
            return
        if not g.user:
            abort(401, description='Entre na sua conta para continuar.')
        if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(
                request.headers.get('X-CSRF-Token', ''), g.auth_session['csrf']):
            abort(403, description='Não foi possível validar sua sessão. Atualize a página e tente novamente.')
        if g.user['must_change_password'] and request.path not in (
                '/api/me', '/api/password', '/api/logout'):
            abort(403, description='Altere sua senha inicial antes de continuar.')
        if request.path.startswith(('/api/boards', '/api/columns', '/api/tasks',
                                    '/api/attachments', '/api/import', '/api/people')):
            need('view_boards')

    @app.after_request
    def finish(response):
        if hasattr(g, 'db'):
            if response.status_code < 400 or getattr(g, 'commit_error', False):
                g.db.commit()
            else:
                g.db.rollback()
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if app.config['COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.teardown_request
    def close(_error):
        if hasattr(g, 'db'):
            g.db.close()

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code == 413:
            if request.path.endswith('/profile'):
                return jsonify(error='A foto deve ter no máximo 5 MB.'), 413
            return jsonify(error='O envio excede o tamanho permitido. O limite por arquivo é de '
                           f'{app.config["MAX_FILE_BYTES"] // (1024 * 1024)} MB; '
                           'Envie arquivos grandes individualmente.'), 413
        defaults = {400:'Solicitação inválida.',401:'Entre na sua conta para continuar.',403:'Você não tem permissão para acessar este recurso.',
                    404:'Recurso não encontrado.',405:'Ação não permitida para este recurso.',415:'Formato de solicitação não permitido.'}
        description = defaults.get(error.code, 'Não foi possível concluir a solicitação.') if error.description == type(error).description else error.description
        return jsonify(error=description), error.code

    @app.errorhandler(sqlite3.IntegrityError)
    def integrity_error(_error):
        return jsonify(error='Este nome já está em uso ou um item relacionado foi alterado. Atualize e tente novamente.'), 409

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.error('Database operation failed: %s', error)
        return jsonify(error='O armazenamento está temporariamente indisponível. Nenhuma alteração foi salva; tente novamente.'), 503

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception('Unexpected request error')
        return jsonify(error='Não foi possível concluir a operação. Nenhuma alteração foi salva.'), 500

    @app.get('/')
    def index():
        return send_from_directory(ROOT / 'dist', 'index.html')

    @app.get('/app.js')
    def javascript():
        return send_from_directory(ROOT / 'dist', 'app.js')

    @app.get('/theme.css')
    def stylesheet():
        with closing(connect(app.config['DATABASE'])) as connection:
            theme = read_theme(connection)
        colors = theme['colors']
        overrides = ':root{' + ''.join(f'--{key}:{value};' for key, value in colors.items()) + '}'
        def contrast(value):
            channels = [int(value[i:i+2],16)/255 for i in (1,3,5)]
            linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in channels]
            return '#111126' if sum(c*w for c,w in zip(linear,(.2126,.7152,.0722))) > .179 else '#ffffff'
        overrides += ':root{--on-accent:' + contrast(colors['yellow']) + ';--on-nav:' + contrast(colors['navy']) + ';}'
        return app.response_class((ROOT / 'dist' / 'theme.css').read_text(encoding='utf-8') + '\n' + overrides,
                                  mimetype='text/css')

    def read_theme(connection):
        row = connection.execute("SELECT value FROM metadata WHERE key='appearance'").fetchone()
        return json.loads(row[0]) if row else {'version': 0, 'colors': {
            'navy': '#08073f', 'yellow': '#e4c204', 'canvas': '#f5f6fa'}}

    @app.get('/api/theme')
    def get_theme():
        return jsonify(theme=read_theme(db()))

    @app.patch('/api/theme')
    def save_theme():
        if g.user['role'] != 'Administrator':
            abort(403, description='Somente administradores podem alterar as cores do sistema.')
        data = body()
        previous = read_theme(db())
        expected(data, previous)
        colors = data.get('colors')
        if not isinstance(colors, dict) or set(colors) != {'navy', 'yellow', 'canvas'} or any(
                not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value) for value in colors.values()):
            abort(400, description='Informe cores válidas no formato #RRGGBB.')
        colors = {key: value.lower() for key, value in colors.items()}
        theme = {'version': previous['version'] + 1, 'colors': colors}
        db().execute("INSERT INTO metadata(key,value) VALUES('appearance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(theme),))
        if colors != previous['colors']:
            audit('settings.theme_changed', 'settings', 'appearance', 'Cores do sistema',
                  previous=previous['colors'], new=colors)
        return jsonify(theme=theme)

    @app.get('/assets/presotto-logo.png')
    def brand_logo():
        return send_from_directory(ROOT / 'dist' / 'assets', 'presotto-logo.png')

    def new_session(user_id):
        db().execute('DELETE FROM sessions WHERE expires_at<=?', (int(time.time()),))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db().execute('INSERT INTO sessions VALUES(?,?,?,?)', (
            hashlib.sha256(token.encode()).hexdigest(), user_id, csrf,
            int(time.time()) + app.config['SESSION_HOURS'] * 3600))
        user = lookup('users', user_id)
        response = jsonify(user=public_user(user), csrf=csrf)
        response.set_cookie('kanban_session', token, httponly=True, secure=app.config['COOKIE_SECURE'],
                            samesite='Strict', max_age=app.config['SESSION_HOURS'] * 3600, path='/')
        return response

    @app.post('/api/login')
    def login():
        data = body()
        username = text(data, 'username', 60)
        supplied = data.get('password', '')
        if not isinstance(supplied, str) or len(supplied) > 256:
            abort(400, description='Credenciais inválidas.')
        timestamp = int(time.time())

        keys = ['account:' + username.casefold(), 'address:' + (request.remote_addr or 'local')]
        db().execute('DELETE FROM login_attempts WHERE window_start<?', (timestamp - 900,))
        for key, limit in zip(keys, [10, 50]):
            attempt = db().execute('SELECT * FROM login_attempts WHERE key=?', (key,)).fetchone()
            if attempt and attempt['attempts'] >= limit:
                g.commit_error = True
                abort(429, description='Muitas tentativas de acesso. Tente novamente em 15 minutos.')
        user = db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        valid = False
        try:
            valid = PH.verify(user['password_hash'] if user else DUMMY_HASH, supplied)
        except (VerificationError, InvalidHashError):
            pass
        if not user or not valid or not user['enabled']:
            for key in keys:
                db().execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) '
                             'DO UPDATE SET attempts=attempts+1', (key, timestamp))
            g.commit_error = True
            abort(401, description='Usuário ou senha incorretos.')
        db().execute('DELETE FROM login_attempts WHERE key=?', (keys[0],))
        if PH.check_needs_rehash(user['password_hash']):
            db().execute('UPDATE users SET password_hash=? WHERE id=?', (PH.hash(supplied), user['id']))
        if g.auth_session:
            db().execute('DELETE FROM sessions WHERE token_hash=?', (g.auth_session['token_hash'],))
        return new_session(user['id'])

    @app.get('/api/me')
    def me():
        return jsonify(user=public_user(g.user), csrf=g.auth_session['csrf'])

    def profile_target(user_id=None):
        if user_id and user_id != g.user['id'] and g.user['role'] != 'Administrator':
            abort(403, description='Somente administradores podem editar o perfil de outra pessoa.')
        return lookup('users', user_id or g.user['id'])

    @app.get('/api/profile')
    @app.get('/api/users/<user_id>/profile')
    def get_profile(user_id=None):
        return jsonify(user=public_user(profile_target(user_id)))

    def optimized_avatar(uploaded):
        suffix = (uploaded.filename or '').rsplit('.', 1)[-1].lower()
        formats = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}
        if suffix not in formats:
            abort(400, description='Selecione uma imagem JPG, JPEG, PNG ou WebP.')
        content = uploaded.read(app.config['MAX_AVATAR_BYTES'] + 1)
        if len(content) > app.config['MAX_AVATAR_BYTES']:
            abort(413, description='A foto deve ter no máximo 5 MB.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != formats[suffix] or image.width * image.height > 16_000_000:
                        abort(400, description='Imagem inválida ou muito grande. Limite de 16 megapixels.')
                    if getattr(image, 'is_animated', False):
                        abort(400, description='Use uma foto sem animação.')
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    oriented = ImageOps.exif_transpose(image)
                    normalized = ImageOps.fit(oriented.convert('RGBA'), (256, 256), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    normalized.save(output, format='WEBP', quality=85, method=4)
                    return output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            abort(400, description='Não foi possível ler a imagem. Selecione uma foto válida.')

    @app.patch('/api/profile')
    @app.patch('/api/users/<user_id>/profile')
    def update_profile(user_id=None):
        target = profile_target(user_id)
        if request.is_json:
            data = body()
        elif request.mimetype == 'multipart/form-data':
            data = request.form.to_dict()
            try:
                data['version'] = int(data.get('version', ''))
            except ValueError:
                abort(400, description='Informe a versão do perfil.')
        else:
            abort(415, description='Envie o perfil como JSON ou formulário multipart.')
        if set(data) - {'version', 'display_name', 'avatar_action'}:
            abort(400, description='Este formulário permite alterar apenas o nome de exibição e a foto.')
        expected(data, {'version': target['profile_version']})
        name = text(data, 'display_name', 80) if 'display_name' in data else target['display_name'] or target['username']
        if any(ord(c) < 32 or ord(c) == 127 for c in name):
            abort(400, description='Nome de exibição inválido.')
        action = data.get('avatar_action', 'keep')
        if action not in ('keep', 'remove', 'replace'):
            abort(400, description='Ação de foto inválida.')
        files = request.files.getlist('avatar')
        if set(request.files) - {'avatar'} or (action == 'replace' and len(files) != 1) or (action != 'replace' and files):
            abort(400, description='Selecione uma única foto.')
        avatar = optimized_avatar(files[0]) if action == 'replace' else None if action == 'remove' else target['avatar']
        name_changed = name != (target['display_name'] or target['username'])
        photo_changed = avatar != target['avatar']
        if name_changed or photo_changed:
            db().execute('UPDATE users SET display_name=?,avatar=?,avatar_version=avatar_version+?,'
                         'profile_version=profile_version+1 WHERE id=?', (name, avatar, int(photo_changed), target['id']))
            if name_changed:
                audit('user.profile_updated', 'user', target['id'], name,
                      previous={'display_name': target['display_name'] or target['username']}, new={'display_name': name})
            if photo_changed:
                audit('user.avatar_changed', 'user', target['id'], name,
                      previous={'has_avatar': bool(target['avatar']), 'avatar_version': target['avatar_version']},
                      new={'has_avatar': bool(avatar), 'avatar_version': target['avatar_version'] + 1})
        return jsonify(user=public_user(lookup('users', target['id']), g.user['role'] == 'Administrator'))

    @app.get('/api/users/<user_id>/avatar')
    def user_avatar(user_id):
        if user_id != g.user['id'] and g.user['role'] != 'Administrator':
            need('view_boards')
            need('view_task_assignees')
        target = lookup('users', user_id)
        if not target['avatar']:
            abort(404, description='Este usuário não possui foto.')
        return send_file(io.BytesIO(target['avatar']), mimetype='image/webp', max_age=0)

    @app.post('/api/logout')
    def logout():
        db().execute('DELETE FROM sessions WHERE token_hash=?', (g.auth_session['token_hash'],))
        response = jsonify(ok=True)
        response.delete_cookie('kanban_session', path='/')
        return response

    @app.post('/api/password')
    def change_password():
        data = body()
        old = data.get('current_password')
        if not isinstance(old, str) or len(old) > 256:
            abort(400, description='Informe sua senha atual.')
        try:
            PH.verify(g.user['password_hash'], old)
        except (VerificationError, InvalidHashError):
            abort(400, description='A senha atual está incorreta.')
        new = password(data, 'new_password')
        if data.get('confirm_password') != new:
            abort(400, description='As novas senhas não coincidem.')
        if new == old:
            abort(400, description='Escolha uma senha diferente.')
        db().execute('UPDATE users SET password_hash=?,must_change_password=0,version=version+1 WHERE id=?',
                     (PH.hash(new), g.user['id']))
        db().execute('DELETE FROM sessions WHERE user_id=?', (g.user['id'],))
        audit('user.password_changed', 'user', g.user['id'], g.user['display_name'] or g.user['username'], new={'changed': True})
        return new_session(g.user['id'])

    @app.get('/api/config')
    def configuration():
        return jsonify(permissions=PERMISSIONS, roles=ROLES, max_file_bytes=app.config['MAX_FILE_BYTES'],
                       extensions=[], allow_all_files=True, activity_actions=ACTIVITY_ACTIONS,
                       max_avatar_bytes=app.config['MAX_AVATAR_BYTES'])

    @app.get('/api/people')
    def people():
        need('view_boards')
        if not {'view_task_assignees', 'assign_users'} & set(effective(g.user)):
            return jsonify(users=[{'id': g.user['id'], 'username': g.user['username'], 'enabled': 1, **profile_identity(g.user)}])
        visible = 'view_task_assignees' in effective(g.user) or g.user['role'] == 'Administrator'
        return jsonify(users=[{'id': r['id'], 'username': r['username'], 'enabled': r['enabled'], **profile_identity(r, visible)}
                              for r in db().execute('SELECT * FROM users ORDER BY COALESCE(NULLIF(display_name,\'\'),username),id')])

    @app.get('/api/users')
    def users():
        if not any(p in effective(g.user) for p in ('manage_users', 'manage_permissions')):
            abort(403)
        return jsonify(users=[public_user(u, True) for u in db().execute('SELECT * FROM users ORDER BY username')])

    def user_fields(data):
        name = text(data, 'username', 60)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,60}', name):
            abort(400, description='O nome de usuário pode conter letras, números, pontos, sublinhados e hífens.')
        return name

    def permission_fields(data):
        role = data.get('role', 'Member')
        overrides = data.get('overrides', {})
        if not isinstance(role, str) or role not in ROLES or not isinstance(overrides, dict) or any(
                p not in PERMISSIONS or type(v) is not bool for p, v in overrides.items()):
            abort(400, description='Perfil ou permissões individuais inválidos.')
        return role, json.dumps(overrides)

    @app.post('/api/users')
    def create_user():
        need('manage_users')
        data = body()
        name, secret = user_fields(data), password(data)
        role, overrides = permission_fields(data)
        if role != 'Member' or json.loads(overrides):
            need('manage_permissions')
        if role == 'Administrator' and g.user['role'] != 'Administrator':
            abort(403, description='Somente um administrador pode criar contas de administrador.')
        enabled = data.get('enabled', True)
        if type(enabled) is not bool:
            abort(400, description='Status da conta inválido.')
        user_id = uid()
        db().execute('INSERT INTO users(id,username,password_hash,role,permissions,enabled,created_at) '
                     'VALUES(?,?,?,?,?,?,?)', (user_id, name, PH.hash(secret), role, overrides, int(enabled), now()))
        audit('user.created', 'user', user_id, name,
              new={'username': name, 'role': role, 'overrides': json.loads(overrides), 'enabled': enabled})
        return jsonify(user=public_user(lookup('users', user_id), True)), 201

    def protect_admin(target, role, enabled, deleting=False):
        if target['id'] == g.user['id']:
            abort(403, description='Use seu perfil para alterar a senha. Você não pode modificar ou excluir sua própria conta nesta tela.')
        if target['role'] == 'Administrator' and g.user['role'] != 'Administrator':
            abort(403, description='Somente administradores podem modificar outras contas de administrador.')
        if target['role'] == 'Administrator' and target['enabled'] and (
                deleting or role != 'Administrator' or not enabled):
            remaining = db().execute("SELECT COUNT(*) FROM users WHERE role='Administrator' AND enabled=1 AND id!=?",
                                     (target['id'],)).fetchone()[0]
            if not remaining:
                abort(409, description='É necessário manter pelo menos um administrador ativo.')

    @app.patch('/api/users/<user_id>')
    def update_user(user_id):
        data = body()
        target = lookup('users', user_id)
        expected(data, target)
        name, enabled = target['username'], bool(target['enabled'])
        role, overrides = target['role'], target['permissions']
        if 'username' in data or 'enabled' in data:
            need('manage_users')
            if 'username' in data:
                name = user_fields(data)
            enabled = data.get('enabled', enabled)
            if type(enabled) is not bool:
                abort(400, description='Status da conta inválido.')
        if 'role' in data or 'overrides' in data:
            need('manage_permissions')
            role, overrides = permission_fields({'role': data.get('role', role),
                                                'overrides': data.get('overrides', json.loads(overrides))})
        if not any(k in data for k in ('username', 'enabled', 'role', 'overrides')):
            abort(400, description='Nenhuma alteração foi informada.')
        protect_admin(target, role, enabled)
        if role == 'Administrator' and g.user['role'] != 'Administrator':
            abort(403, description='Somente um administrador pode conceder o perfil de Administrador.')
        db().execute('UPDATE users SET username=?,enabled=?,role=?,permissions=?,version=version+1 WHERE id=?',
                     (name, int(enabled), role, overrides, user_id))

        db().execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        if name != target['username'] or enabled != bool(target['enabled']):
            audit('user.updated', 'user', user_id, name,
                  previous={'username': target['username'], 'enabled': bool(target['enabled'])},
                  new={'username': name, 'enabled': enabled})
        if role != target['role']:
            audit('user.role_changed', 'user', user_id, name,
                  previous={'role': target['role']}, new={'role': role})
        if json.loads(overrides) != json.loads(target['permissions']) or role != target['role']:
            audit('user.permissions_changed', 'user', user_id, name,
                  previous={'overrides': json.loads(target['permissions']), 'effective': effective(target)},
                  new={'overrides': json.loads(overrides), 'effective': effective(lookup('users', user_id))})
        return jsonify(user=public_user(lookup('users', user_id), True))

    @app.delete('/api/users/<user_id>')
    def delete_user(user_id):
        need('manage_users')
        target = lookup('users', user_id)
        expected(body(), target)
        protect_admin(target, target['role'], bool(target['enabled']), deleting=True)
        for task in db().execute('SELECT t.* FROM tasks t JOIN task_assignees a ON t.id=a.task_id '
                                 'WHERE a.user_id=?', (user_id,)).fetchall():
            audit('task.unassigned', 'task', task['id'], task['title'], board_id=task['board_id'], task=task,
                  previous={'user_id': user_id, 'username': target['username'],
                            'display_name': target['display_name'] or target['username'], 'reason': 'account_deleted'})
            touch(task['board_id'])
        audit('user.deleted', 'user', user_id, target['username'],
              previous={'username': target['username'], 'role': target['role'],
                        'overrides': json.loads(target['permissions']), 'enabled': bool(target['enabled'])})
        db().execute('DELETE FROM users WHERE id=?', (user_id,))
        return jsonify(ok=True)

    @app.post('/api/users/<user_id>/password')
    def reset_password(user_id):
        need('manage_users')
        data = body()
        target = lookup('users', user_id)
        expected(data, target)
        protect_admin(target, target['role'], bool(target['enabled']))
        db().execute('UPDATE users SET password_hash=?,must_change_password=1,version=version+1 WHERE id=?',
                     (PH.hash(password(data)), user_id))
        db().execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        audit('user.password_reset', 'user', user_id, target['display_name'] or target['username'], new={'reset': True})
        return jsonify(user=public_user(lookup('users', user_id), True))

    @app.get('/api/boards')
    def boards():
        need('view_boards')
        return jsonify(boards=[dict(b) for b in db().execute('SELECT id,name,position,version FROM boards ORDER BY position,id')])

    @app.get('/api/boards/<board_id>')
    def board_details(board_id):
        need('view_boards')
        return jsonify(board=serialize_board(board_id))

    @app.post('/api/boards')
    def create_board():
        need('manage_boards')
        name = text(body(), 'name', 100)
        board_id = uid()
        position = db().execute('SELECT COALESCE(MAX(position),-1)+1 FROM boards').fetchone()[0]
        db().execute('INSERT INTO boards(id,name,position,created_by,created_at) VALUES(?,?,?,?,?)',
                     (board_id, name, position, g.user['id'], now()))
        audit('board.created', 'board', board_id, name, board_id=board_id, new={'name': name, 'position': position})
        for pos, (label, kind) in enumerate([('A fazer', 'todo'), ('Em andamento', 'doing'), ('Concluído', 'done')]):
            column_id = uid()
            db().execute('INSERT INTO columns VALUES(?,?,?,?,?)', (column_id, board_id, label, kind, pos))
            audit('column.created', 'column', column_id, label, board_id=board_id,
                  new={'name': label, 'type': kind, 'position': pos})
        return jsonify(board=serialize_board(board_id)), 201

    @app.patch('/api/boards/<board_id>')
    def edit_board(board_id):
        need('manage_boards')
        data = body()
        board = board_write(board_id, data)
        name = text(data, 'name', 100)
        db().execute('UPDATE boards SET name=?,version=version+1 WHERE id=?', (name, board_id))
        if name != board['name']:
            audit('board.updated', 'board', board_id, name, board_id=board_id,
                  previous={'name': board['name']}, new={'name': name})
        return jsonify(board=serialize_board(board_id))

    @app.delete('/api/boards/<board_id>')
    def delete_board(board_id):
        need('manage_boards')
        board = board_write(board_id, body())
        if db().execute('SELECT COUNT(*) FROM boards').fetchone()[0] <= 1:
            abort(409, description='Mantenha pelo menos um quadro.')
        for task in db().execute('SELECT * FROM tasks WHERE board_id=?', (board_id,)).fetchall():
            log_task_deletion(task)
        for col in db().execute('SELECT * FROM columns WHERE board_id=?', (board_id,)).fetchall():
            audit('column.deleted', 'column', col['id'], col['name'], board_id=board_id,
                  previous={'name': col['name'], 'type': col['type'], 'position': col['position']})
        audit('board.deleted', 'board', board_id, board['name'], board_id=board_id,
              previous={'name': board['name'], 'position': board['position']})
        db().execute('DELETE FROM tasks WHERE board_id=?', (board_id,))
        db().execute('DELETE FROM boards WHERE id=?', (board_id,))
        return jsonify(ok=True)

    def column_values(data):
        name = text(data, 'name', 60)
        kind = data.get('type', 'todo')
        if kind not in ('todo', 'doing', 'done'):
            abort(400, description='Tipo de coluna inválido.')
        return name, kind

    @app.post('/api/boards/<board_id>/columns')
    def create_column(board_id):
        need('manage_columns')
        data = body()
        board_write(board_id, data)
        name, kind = column_values(data)
        position = db().execute('SELECT COALESCE(MAX(position),-1)+1 FROM columns WHERE board_id=?', (board_id,)).fetchone()[0]
        column_id = uid()
        db().execute('INSERT INTO columns VALUES(?,?,?,?,?)', (column_id, board_id, name, kind, position))
        audit('column.created', 'column', column_id, name, board_id=board_id,
              new={'name': name, 'type': kind, 'position': position})
        touch(board_id)
        return jsonify(board=serialize_board(board_id)), 201

    @app.patch('/api/columns/<column_id>')
    def edit_column(column_id):
        need('manage_columns')
        data = body()
        col = lookup('columns', column_id)
        board_write(col['board_id'], data)
        name, kind = column_values(data)
        db().execute('UPDATE columns SET name=?,type=? WHERE id=?', (name, kind, column_id))
        if name != col['name'] or kind != col['type']:
            audit('column.updated', 'column', column_id, name, board_id=col['board_id'],
                  previous={'name': col['name'], 'type': col['type']}, new={'name': name, 'type': kind})
        touch(col['board_id'])
        return jsonify(board=serialize_board(col['board_id']))

    @app.post('/api/columns/<column_id>/move')
    def move_column(column_id):
        need('manage_columns')
        data = body()
        col = lookup('columns', column_id)
        board_write(col['board_id'], data)
        ids = [r[0] for r in db().execute('SELECT id FROM columns WHERE board_id=? ORDER BY position,id', (col['board_id'],))]
        destination = data.get('position')
        if type(destination) is not int or not 0 <= destination < len(ids):
            abort(400, description='Posição de coluna inválida.')
        ids.remove(column_id)
        ids.insert(destination, column_id)
        db().executemany('UPDATE columns SET position=? WHERE id=?', [(pos, i) for pos, i in enumerate(ids)])
        if col['position'] != destination:
            audit('column.reordered', 'column', column_id, col['name'], board_id=col['board_id'],
                  previous={'position': col['position']}, new={'position': destination})
        touch(col['board_id'])
        return jsonify(board=serialize_board(col['board_id']))

    @app.delete('/api/columns/<column_id>')
    def delete_column(column_id):
        need('delete_columns')
        data = body()
        col = lookup('columns', column_id)
        board_id = col['board_id']
        board_write(board_id, data)
        target_id = data.get('target_id')
        if target_id == column_id:
            abort(400, description='Escolha outra coluna de destino.')
        target = column_for(board_id, target_id)
        position = db().execute('SELECT COALESCE(MAX(position),-1)+1 FROM tasks WHERE column_id=?', (target_id,)).fetchone()[0]
        moving = list(db().execute('SELECT * FROM tasks WHERE column_id=? ORDER BY position,id', (column_id,)))
        for n, task in enumerate(moving):
            audit('task.moved', 'task', task['id'], task['title'], board_id=board_id, task=task,
                  previous={'column_id': column_id, 'column_name': col['name'], 'position': task['position']},
                  new={'column_id': target_id, 'column_name': target['name'], 'position': position + n})
        db().executemany('UPDATE tasks SET column_id=?,position=?,updated_at=? WHERE id=?',
                         [(target_id, position + n, now(), r['id']) for n, r in enumerate(moving)])
        audit('column.deleted', 'column', column_id, col['name'], board_id=board_id,
              previous={'name': col['name'], 'type': col['type'], 'position': col['position']},
              new={'destination_column_id': target_id, 'destination_column_name': target['name']})
        db().execute('DELETE FROM columns WHERE id=?', (column_id,))
        touch(board_id)
        return jsonify(board=serialize_board(board_id))

    def task_values(data):
        title = text(data, 'title', 150)
        description = text(data, 'description', 20000, False)
        priority = data.get('priority', 'normal')
        if priority not in ('normal', 'high', 'low'):
            abort(400, description='Prioridade inválida.')
        return title, description, priority

    def registered_task_tags(task_id):
        return [dict(row) for row in db().execute(
            'SELECT t.id,t.name,t.color,t.active FROM tags t JOIN task_tags a ON a.tag_id=t.id '
            'WHERE a.task_id=? ORDER BY t.name_key,t.id', (task_id,))]

    def task_tags(data, existing=None):
        current = {t['id'] for t in registered_task_tags(existing['id'])} if existing is not None else set()
        if 'tag_ids' not in data and 'tags' not in data:
            return current
        values = data.get('tag_ids', data.get('tags'))
        if not isinstance(values, list) or len(values) > 20 or any(not isinstance(v, str) for v in values):
            abort(400, description='Informe no máximo 20 tags cadastradas por tarefa.')
        desired = set()
        for value in values:
            tag = db().execute('SELECT * FROM tags WHERE ' + ('id' if 'tag_ids' in data else 'name_key') + '=?',
                               (value if 'tag_ids' in data else value.strip().casefold(),)).fetchone()
            if not tag:
                abort(400, description='Selecione apenas tags já cadastradas.')
            if tag['id'] not in current and not tag['active']:
                abort(400, description='Não é possível atribuir uma tag inativa.')
            desired.add(tag['id'])
        if desired - current:
            need('assign_tags')
        if current - desired:
            need('remove_tags')
        return desired

    def save_task_tags(task, desired):
        current = {t['id'] for t in registered_task_tags(task['id'])}
        for tag_id in sorted(current ^ desired):
            tag = lookup('tags', tag_id)
            values = {'tag_id': tag_id, 'name': tag['name'], 'color': tag['color']}
            added = tag_id in desired
            if added:
                db().execute('INSERT INTO task_tags VALUES(?,?)', (task['id'], tag_id))
            else:
                db().execute('DELETE FROM task_tags WHERE task_id=? AND tag_id=?', (task['id'], tag_id))
            audit('task.tag_added' if added else 'task.tag_removed', 'task', task['id'], task['title'],
                  board_id=task['board_id'], task=task, previous=None if added else values, new=values if added else None)

    @app.patch('/api/tasks/<task_id>/tags')
    def update_task_tags(task_id):
        if not {'assign_tags', 'remove_tags'} & set(effective(g.user)):
            abort(403)
        data = body()
        task = lookup('tasks', task_id)
        board_write(task['board_id'], data)
        save_task_tags(task, task_tags(data, task))
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    def tag_public(tag):
        value = dict(tag)
        value.pop('name_key', None)
        value['active'] = bool(value['active'])
        value['task_count'] = db().execute('SELECT COUNT(*) FROM task_tags WHERE tag_id=?', (tag['id'],)).fetchone()[0]
        return value

    @app.get('/api/tags')
    def list_tags():
        if not {'view_tags', 'create_tags', 'edit_tags', 'delete_tags', 'manage_tag_status'} & set(effective(g.user)):
            abort(403)
        return jsonify(tags=[tag_public(t) for t in db().execute('SELECT * FROM tags ORDER BY name_key,id')])

    def tag_values(data, existing=None):
        base = dict(existing) if existing else {'name': '', 'color': '#475569', 'description': '', 'active': True}
        base['active'] = bool(base['active'])
        values = {k: data.get(k, base[k]) for k in ('name', 'color', 'description', 'active')}
        name = text(values, 'name', 40)
        if any(ord(c) < 32 or ord(c) == 127 for c in name) or ',' in name:
            abort(400, description='Nome de tag inválido.')
        description = text(values, 'description', 2000, False)
        if not isinstance(values['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', values['color']):
            abort(400, description='Cor de tag inválida.')
        if type(values['active']) is not bool:
            abort(400, description='Status de tag inválido.')
        duplicate = db().execute('SELECT id FROM tags WHERE name_key=?', (name.casefold(),)).fetchone()
        if duplicate and (not existing or duplicate['id'] != existing['id']):
            abort(409, description='Já existe uma tag com este nome.')
        return {'name': name, 'color': values['color'].lower(), 'description': description, 'active': values['active']}

    @app.post('/api/tags')
    def create_tag():
        need('create_tags')
        values = tag_values(body())
        if not values['active']:
            need('manage_tag_status')
        tag_id = uid()
        db().execute('INSERT INTO tags(id,name,name_key,color,description,active,created_by,creator_name,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (tag_id, values['name'], values['name'].casefold(), values['color'], values['description'], values['active'], g.user['id'], g.user['username'], now()))
        audit('tag.created', 'tag', tag_id, values['name'], new=values)
        return jsonify(tag=tag_public(lookup('tags', tag_id))), 201

    @app.patch('/api/tags/<tag_id>')
    def edit_tag(tag_id):
        data = body()
        tag = lookup('tags', tag_id)
        expected(data, tag)
        values = tag_values(data, tag)
        previous = {k: tag[k] for k in values}
        if any(values[k] != tag[k] for k in ('name', 'color', 'description')):
            need('edit_tags')
        if values['active'] != bool(tag['active']):
            need('manage_tag_status')
        if not {'edit_tags', 'manage_tag_status'} & set(effective(g.user)):
            abort(403)
        db().execute('UPDATE tags SET name=?,name_key=?,color=?,description=?,active=?,version=version+1 WHERE id=?',
                     (values['name'], values['name'].casefold(), values['color'], values['description'], values['active'], tag_id))
        if any(values[k] != tag[k] for k in ('name', 'color', 'description')):
            audit('tag.updated', 'tag', tag_id, values['name'], previous=previous, new=values)
        if values['active'] != bool(tag['active']):
            audit('tag.activated' if values['active'] else 'tag.deactivated', 'tag', tag_id, values['name'], previous=previous, new=values)
        touch_tag_boards(tag_id)
        return jsonify(tag=tag_public(lookup('tags', tag_id)))

    def touch_tag_boards(tag_id):
        for row in db().execute('SELECT DISTINCT t.board_id FROM tasks t JOIN task_tags a ON a.task_id=t.id WHERE a.tag_id=?', (tag_id,)).fetchall():
            touch(row['board_id'])

    @app.delete('/api/tags/<tag_id>')
    def delete_tag(tag_id):
        need('delete_tags')
        tag = lookup('tags', tag_id)
        expected(body(), tag)
        for row in db().execute('SELECT t.* FROM tasks t JOIN task_tags a ON a.task_id=t.id WHERE a.tag_id=?', (tag_id,)).fetchall():
            audit('task.tag_removed', 'task', row['id'], row['title'], board_id=row['board_id'], task=row,
                  previous={'tag_id': tag_id, 'name': tag['name'], 'color': tag['color'], 'reason': 'tag_deleted'})
        audit('tag.deleted', 'tag', tag_id, tag['name'], previous=tag_public(tag))
        touch_tag_boards(tag_id)
        db().execute('DELETE FROM tags WHERE id=?', (tag_id,))
        return jsonify(ok=True)

    def task_dates(data, existing=None):
        dates = {}
        for key in ('task_date', 'desired_delivery'):
            value = data[key] if key in data else existing[key] if existing is not None else None
            if value is None or value == '':
                dates[key] = None
                continue
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                abort(400, description='Data inválida. Use o formato AAAA-MM-DD.')
            try:
                datetime.strptime(value, '%Y-%m-%d')
            except ValueError:
                abort(400, description='Informe uma data válida.')
            dates[key] = value
        return dates

    @app.post('/api/boards/<board_id>/tasks')
    def create_task(board_id):
        need('create_tasks')
        data = body()
        board_write(board_id, data)
        column_id = data.get('column_id')
        column_for(board_id, column_id)
        title, description, priority = task_values(data)
        dates = task_dates(data)
        tags = task_tags(data)
        position = db().execute('SELECT COALESCE(MAX(position),-1)+1 FROM tasks WHERE column_id=?', (column_id,)).fetchone()[0]
        task_id = uid()
        db().execute('INSERT INTO tasks(id,board_id,column_id,title,description,priority,position,created_by,created_at,updated_at,task_date,desired_delivery) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (task_id, board_id, column_id, title, description, priority, position, g.user['id'], now(), now(), dates['task_date'], dates['desired_delivery']))
        task = lookup('tasks', task_id)
        save_task_tags(task, tags)
        audit('task.created', 'task', task_id, title, board_id=board_id, task=task, new=task_snapshot(task))
        assign(task_id, data)
        touch(board_id)
        return jsonify(board=serialize_board(board_id), task_id=task_id), 201

    @app.patch('/api/tasks/<task_id>')
    def edit_task(task_id):
        need('edit_tasks')
        data = body()
        task = lookup('tasks', task_id)
        board_write(task['board_id'], data)
        title, description, priority = task_values(data)
        dates = task_dates(data, task)
        tags = task_tags(data, task)

        if 'column_id' in data and data['column_id'] != task['column_id']:
            abort(400, description='Use a ação de mover para alterar a coluna.')
        db().execute('UPDATE tasks SET title=?,description=?,priority=?,task_date=?,desired_delivery=?,updated_at=? WHERE id=?',
                     (title, description, priority, dates['task_date'], dates['desired_delivery'], now(), task_id))
        before = {k: task[k] for k in ('title', 'description', 'priority', 'task_date', 'desired_delivery')}
        save_task_tags(lookup('tasks', task_id), tags)
        after = {'title': title, 'description': description, 'priority': priority, **dates}
        if before != after:
            audit('task.updated', 'task', task_id, title, board_id=task['board_id'],
                  task=lookup('tasks', task_id), previous=before, new=after)
        assign(task_id, data)
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    @app.patch('/api/tasks/<task_id>/assignees')
    def update_assignees(task_id):
        permissions = effective(g.user)
        if not {'assign_users', 'assign_self'} & set(permissions):
            abort(403, description='Você não tem permissão para atribuir responsáveis.')
        data = body()
        task = lookup('tasks', task_id)
        board_write(task['board_id'], data)
        add, remove = data.get('add', []), data.get('remove', [])
        if any(not isinstance(items, list) or len(items) > 100 or any(not isinstance(i, str) for i in items)
               for items in (add, remove)) or set(add) & set(remove):
            abort(400, description='Alterações de responsáveis inválidas.')
        requested = set(add) | set(remove)
        if 'assign_users' not in permissions and requested - {g.user['id']}:
            abort(403, description='Você pode atribuir ou remover apenas a si mesmo.')
        current = {r[0] for r in db().execute('SELECT user_id FROM task_assignees WHERE task_id=?', (task_id,))}
        assign(task_id, {'assignees': sorted((current | set(add)) - set(remove))})
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    @app.post('/api/tasks/<task_id>/move')
    def move_task(task_id):
        need('move_tasks')
        data = body()
        task = lookup('tasks', task_id)
        board_write(task['board_id'], data)
        target_id = data.get('column_id')
        column_for(task['board_id'], target_id)
        ids = [r[0] for r in db().execute('SELECT id FROM tasks WHERE column_id=? AND id!=? ORDER BY position,id',
                                         (target_id, task_id))]
        before = data.get('before_id')
        if before is not None and before not in ids:
            abort(409, description='A tarefa de destino foi alterada. Atualize e tente novamente.')
        ids.insert(ids.index(before) if before else len(ids), task_id)
        db().execute('UPDATE tasks SET column_id=?,updated_at=? WHERE id=?', (target_id, now(), task_id))
        db().executemany('UPDATE tasks SET position=? WHERE id=?', [(pos, i) for pos, i in enumerate(ids)])
        target = lookup('columns', target_id)
        previous_column = lookup('columns', task['column_id'])
        if task['column_id'] != target_id or task['position'] != ids.index(task_id):
            audit('task.moved', 'task', task_id, task['title'], board_id=task['board_id'], task=task,
                  previous={'column_id': task['column_id'], 'column_name': previous_column['name'], 'position': task['position']},
                  new={'column_id': target_id, 'column_name': target['name'], 'position': ids.index(task_id)})
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    @app.delete('/api/tasks/<task_id>')
    def delete_task(task_id):
        need('delete_tasks')
        task = lookup('tasks', task_id)
        board_write(task['board_id'], body())
        log_task_deletion(task)
        db().execute('DELETE FROM tasks WHERE id=?', (task_id,))
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    def validate_file(filename, content):
        name = filename.replace('\\', '/').split('/')[-1]
        name = ''.join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127).strip()
        if not name or name in ('.', '..') or len(name) > 200:
            abort(400, description='Nome de arquivo inválido.')
        if not content:
            abort(400, description='Não é possível enviar arquivos vazios.')
        ext = name.rsplit('.', 1)[1].lower() if '.' in name else ''
        return name, ext or 'arquivo'

    @app.post('/api/tasks/<task_id>/attachments')
    def upload_attachment(task_id):
        need('upload_attachments')
        task = lookup('tasks', task_id)
        version = request.headers.get('If-Match', '')
        if not version.isdigit():
            abort(400, description='Informe a versão do quadro no cabeçalho If-Match.')
        board_write(task['board_id'], {'version': int(version)})
        files = request.files.getlist('file')
        if not files or len(files) > 10:
            abort(400, description='Selecione de 1 a 10 arquivos por envio.')
        for uploaded in files:
            content = uploaded.read(app.config['MAX_FILE_BYTES'] + 1)
            if len(content) > app.config['MAX_FILE_BYTES']:
                abort(413, description='O arquivo excede o tamanho máximo permitido.')
            name, ext = validate_file(uploaded.filename or '', content)
            attachment_id = uid()
            db().execute('INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?)',
                         (attachment_id, task_id, name, ext, len(content), hashlib.sha256(content).hexdigest(),
                          g.user['id'], now(), content))
            attachment = lookup('attachments', attachment_id)
            audit('attachment.uploaded', 'attachment', attachment_id, name, board_id=task['board_id'],
                  task=task, new=file_snapshot(attachment))
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id'])), 201

    @app.get('/api/attachments/<attachment_id>/download')
    def download_attachment(attachment_id):
        need('download_attachments')
        attachment = lookup('attachments', attachment_id)
        return send_file(io.BytesIO(attachment['content']), mimetype='application/octet-stream',
                         as_attachment=True, download_name=attachment['filename'], max_age=0)

    @app.delete('/api/attachments/<attachment_id>')
    def delete_attachment(attachment_id):
        attachment = lookup('attachments', attachment_id)
        require_file_deletion(attachment)
        task = lookup('tasks', attachment['task_id'])
        board_write(task['board_id'], body())
        audit('attachment.deleted', 'attachment', attachment_id, attachment['filename'],
              board_id=task['board_id'], task=task, previous=file_snapshot(attachment))
        db().execute('DELETE FROM attachments WHERE id=?', (attachment_id,))
        touch(task['board_id'])
        return jsonify(board=serialize_board(task['board_id']))

    def activity_visibility():
        clauses = []
        permissions = effective(g.user)
        if 'view_task_assignees' not in permissions:
            clauses.append("action NOT IN ('task.assigned','task.unassigned')")
        if 'download_attachments' not in permissions:
            clauses.append("entity_type != 'attachment'")
        if 'view_tags' not in permissions:
            clauses.append("entity_type != 'tag' AND action NOT IN ('task.tag_added','task.tag_removed')")
        return clauses

    def activity_results(task_id=None):
        clauses, values = activity_visibility(), []
        if task_id:
            clauses.append('task_id=?')
            values.append(task_id)
        for parameter, field in [('actor_id', 'actor_id'), ('task_id', 'task_id')]:
            value = request.args.get(parameter)
            if value and not (task_id and parameter == 'task_id'):
                if len(value) > 100:
                    abort(400, description='Filtro de atividade inválido.')
                clauses.append(f'{field}=?')
                values.append(value)
        action = request.args.get('action')
        if action:
            if action in ACTIVITY_ACTIONS:
                clauses.append('action=?')
                values.append(action)
            elif action in ('task', 'column', 'attachment', 'user', 'board'):
                clauses.append('entity_type=?')
                values.append(action)
            else:
                abort(400, description='Filtro de ação inválido.')
        dates = {}
        for parameter, operator in [('start', '>='), ('end', '<')]:
            value = request.args.get(parameter)
            if value:
                try:
                    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        raise ValueError('Timezone required')
                    dates[parameter] = stamp.astimezone(timezone.utc).isoformat()
                except ValueError:
                    abort(400, description='As datas devem usar o formato ISO com fuso horário.')
                clauses.append(f'occurred_at{operator}?')
                values.append(dates[parameter])
        if 'start' in dates and 'end' in dates and dates['start'] >= dates['end']:
            abort(400, description='A data final deve ser posterior à data inicial.')
        try:
            limit = int(request.args.get('limit', '50'))
            cursor = int(request.args['before']) if 'before' in request.args else None
            if not 1 <= limit <= 100 or (cursor is not None and cursor < 1):
                raise ValueError('Invalid page')
        except ValueError:
            abort(400, description='Página de atividades inválida.')
        if cursor is not None:
            clauses.append('id<?')
            values.append(cursor)
        where = ' AND '.join(clauses) or '1=1'
        rows = list(db().execute(f'SELECT * FROM activity_log WHERE {where} ORDER BY id DESC LIMIT ?',
                                  (*values, limit + 1)))
        entries = []
        for row in rows[:limit]:
            entry = dict(row)
            entry['previous'] = json.loads(entry.pop('previous_values'))
            entry['new'] = json.loads(entry.pop('new_values'))
            actor = db().execute('SELECT display_name,username FROM users WHERE id=?', (entry['actor_id'],)).fetchone()
            entry['actor_display_name'] = actor['display_name'] or actor['username'] if actor else entry['actor_name']
            if entry['entity_type'] == 'user':
                subject = db().execute('SELECT display_name,username FROM users WHERE id=?', (entry['entity_id'],)).fetchone()
                entry['entity_display_name'] = subject['display_name'] or subject['username'] if subject else entry['entity_name']
            for snapshot in (entry['previous'], entry['new']):
                if 'view_tags' not in effective(g.user):
                    snapshot.pop('tags', None)
                if entry['action'] in ('task.assigned', 'task.unassigned') and snapshot.get('user_id'):
                    account = db().execute('SELECT display_name,username FROM users WHERE id=?', (snapshot['user_id'],)).fetchone()
                    if account:
                        snapshot['display_name'] = account['display_name'] or account['username']
            entries.append(entry)
        return jsonify(entries=entries, next_cursor=entries[-1]['id'] if len(rows) > limit else None)

    @app.get('/api/activity')
    def activity_log():
        need('view_activity_log')
        return activity_results()

    @app.get('/api/activity/options')
    def activity_options():
        need('view_activity_log')
        where = ' AND '.join(activity_visibility()) or '1=1'
        actors = [dict(r) for r in db().execute(
            f'SELECT a.actor_id AS id,a.actor_name AS name FROM activity_log a JOIN '
            f'(SELECT MAX(id) AS id FROM activity_log WHERE {where} GROUP BY actor_id) latest '
            'ON latest.id=a.id ORDER BY a.actor_name')]
        for actor in actors:
            account = db().execute('SELECT display_name,username FROM users WHERE id=?', (actor['id'],)).fetchone()
            if account:
                actor['name'] = account['display_name'] or account['username']
        tasks = [dict(r) for r in db().execute(
            f'SELECT a.task_id AS id,a.task_name AS name FROM activity_log a JOIN '
            f'(SELECT MAX(id) AS id FROM activity_log WHERE ({where}) AND task_id IS NOT NULL GROUP BY task_id) latest '
            'ON latest.id=a.id ORDER BY a.task_name')]
        return jsonify(actors=actors, tasks=tasks)

    @app.get('/api/tasks/<task_id>/history')
    def task_history(task_id):
        need('view_task_history')

        return activity_results(task_id)

    @app.post('/api/import')
    def import_browser_board():
        need('manage_boards')
        need('manage_columns')
        need('create_tasks')
        data = body()

        payload = data.get('board')
        if not isinstance(payload, dict):
            abort(400, description='Quadro do navegador inválido.')
        marker = 'import:' + g.user['id'] + ':' + hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()
        previous = db().execute('SELECT value FROM metadata WHERE key=?', (marker,)).fetchone()
        if previous and db().execute('SELECT 1 FROM boards WHERE id=?', (previous[0],)).fetchone():
            return jsonify(board_id=previous[0], already_imported=True)
        if previous:
            db().execute('DELETE FROM metadata WHERE key=?', (marker,))
        columns = payload.get('columns')
        tasks = payload.get('tasks')
        if not isinstance(columns, list) or not 1 <= len(columns) <= 100 or not isinstance(tasks, list) or len(tasks) > 10000:
            abort(400, description='Tamanho de quadro inválido.')
        board_id = uid()
        position = db().execute('SELECT COALESCE(MAX(position),-1)+1 FROM boards').fetchone()[0]
        db().execute('INSERT INTO boards(id,name,position,created_by,created_at) VALUES(?,?,?,?,?)',
                     (board_id, 'Quadro importado do navegador', position, g.user['id'], now()))
        audit('board.created', 'board', board_id, 'Quadro importado do navegador', board_id=board_id,
              new={'name': 'Quadro importado do navegador', 'position': position, 'source': 'browser_import'})
        mapping = {}
        for pos, c in enumerate(columns):
            if not isinstance(c, list) or len(c) < 2 or not isinstance(c[0], str) or c[0] in mapping:
                abort(400, description='Coluna importada inválida.')
            name, kind = column_values({'name': c[1], 'type': c[2] if len(c) > 2 else c[0] if c[0] in ('todo','doing','done') else 'todo'})
            column_id = uid()
            mapping[c[0]] = column_id
            db().execute('INSERT INTO columns VALUES(?,?,?,?,?)', (column_id, board_id, name, kind, pos))
            audit('column.created', 'column', column_id, name, board_id=board_id,
                  new={'name': name, 'type': kind, 'position': pos})
        for pos, task in enumerate(tasks):
            if not isinstance(task, dict) or task.get('status') not in mapping:
                abort(400, description='A coluna da tarefa importada é inválida.')
            title, description, priority = task_values(task)
            dates = task_dates(task)
            if 'tags' in task and 'tag_ids' not in task:
                names = task['tags']
                if not isinstance(names, list) or len(names) > 20 or any(not isinstance(name, str) for name in names):
                    abort(400, description='Tags importadas inválidas.')
                for name in names:
                    if not db().execute('SELECT 1 FROM tags WHERE name_key=?', (name.strip().casefold(),)).fetchone():
                        need('create_tags')
                        values = tag_values({'name': name})
                        tag_id = uid()
                        db().execute('INSERT INTO tags(id,name,name_key,color,description,created_by,creator_name,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                     (tag_id, values['name'], values['name'].casefold(), values['color'], values['description'], g.user['id'], g.user['username'], now()))
                        audit('tag.created', 'tag', tag_id, values['name'], new=values)
            tags = task_tags(task)
            task_id = uid()
            db().execute('INSERT INTO tasks(id,board_id,column_id,title,description,priority,position,created_by,created_at,updated_at,task_date,desired_delivery) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (task_id, board_id, mapping[task['status']], title, description, priority,
                          pos, g.user['id'], now(), now(), dates['task_date'], dates['desired_delivery']))
            imported = lookup('tasks', task_id)
            save_task_tags(imported, tags)
            audit('task.created', 'task', task_id, title, board_id=board_id, task=imported, new=task_snapshot(imported))
        db().execute('INSERT INTO metadata VALUES(?,?)', (marker, board_id))
        return jsonify(board_id=board_id), 201

    updater.register(app, need, body, audit)
    return app


if __name__ == '__main__':
    from waitress import serve
    application = create_app()
    host = os.environ.get('KANBAN_HOST', '127.0.0.1')
    port = int(os.environ.get('KANBAN_PORT', '8000'))
    print(f'Produção Presotto: http://{host}:{port}', flush=True)
    serve(application, host=host, port=port, threads=8,
          max_request_body_size=application.config['MAX_CONTENT_LENGTH'])
