import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from updater import atomic

ROOT = Path(__file__).resolve().parent


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if action not in ('start', 'stop'):
        raise ValueError('Ação inválida.')
    database = Path(os.environ.get('KANBAN_DATABASE', ROOT / 'data' / 'kanban.sqlite3')).resolve()
    directory = database.parent / 'updates'
    directory.mkdir(parents=True, exist_ok=True)
    host = os.environ.get('KANBAN_HOST', '127.0.0.1')
    host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    preferences = directory / 'desktop.json'
    saved = json.loads(preferences.read_text()) if preferences.exists() else {}
    port = int(os.environ.get('KANBAN_PORT', saved.get('port', 8000)))
    base = f'http://{host}:{port}'

    def call(path, method='GET'):
        keyfile = directory / 'health-key.json'
        key = json.loads(keyfile.read_text()) if keyfile.exists() else ''
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={'X-Update-Health': key}, method=method), timeout=1) as response:
            return json.load(response)

    def alive():
        try:
            return bool(call('/health').get('version'))
        except (OSError, ValueError):
            return False

    if action == 'stop':
        if alive():
            call('/_update/stop', 'POST')
            for _ in range(40):
                if not alive():
                    time.sleep(.7)
                    return
                time.sleep(.2)
            raise RuntimeError('O servidor não encerrou. Consulte os registros na pasta data/updates.')
        return
    if not alive():
        with socket.socket() as probe:
            if probe.connect_ex((host, port)) == 0:
                if 'KANBAN_PORT' in os.environ:
                    raise RuntimeError(f'A porta {port} está ocupada por outro aplicativo ou outra instalação do Presotto.')
                for candidate in range(8000, 8100):
                    with socket.socket() as available:
                        try:
                            available.bind((host, candidate))
                        except OSError:
                            continue
                    port = candidate
                    base = f'http://{host}:{port}'
                    break
                else:
                    raise RuntimeError('Não há uma porta disponível entre 8000 e 8099.')
        atomic(preferences, {'port': port})
        env = os.environ.copy()
        env['KANBAN_DATABASE'] = str(database)
        env['KANBAN_PORT'] = str(port)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        with (directory / 'launcher.stdout.log').open('ab') as stdout, (directory / 'launcher.stderr.log').open('ab') as stderr:
            process = subprocess.Popen([sys.executable, str(ROOT / 'launcher.py')], cwd=ROOT, env=env,
                                       stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                       creationflags=flags, start_new_session=os.name != 'nt')
        for _ in range(120):
            if alive():
                break
            if process.poll() is not None:
                raise RuntimeError('Não foi possível iniciar. Consulte data/updates/launcher.stderr.log.')
            time.sleep(.25)
        else:
            raise RuntimeError('O servidor demorou para iniciar. Consulte os registros em data/updates.')
    if '--no-browser' not in sys.argv:
        webbrowser.open(base)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if os.name == 'nt' and '--no-browser' not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, str(error), 'Produção Presotto', 0x10)
        else:
            print(str(error), file=sys.stderr)
        sys.exit(1)
