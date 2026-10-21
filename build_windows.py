import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from app import PH, initialize, now, uid
from backup import backup
from build_release import build

ROOT = Path(__file__).resolve().parent
RUNTIME_HASH = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
PACKAGES = ('flask', 'argon2-cffi', 'argon2-cffi-bindings', 'cffi', 'pycparser', 'waitress',
            'pillow', 'werkzeug', 'jinja2', 'markupsafe', 'itsdangerous', 'click', 'blinker')


def seed_database(source, destination):
    if source.is_file():
        backup(source, destination)
    else:
        initialize(destination)
    with closing(sqlite3.connect(destination)) as db, db:
        db.execute('DELETE FROM sessions')
        db.execute('DELETE FROM login_attempts')
        admin = db.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', ('Admin',)).fetchone()
        if admin:
            db.execute("UPDATE users SET username='Admin',password_hash=?,role='Administrator',permissions='{}',enabled=1,must_change_password=1 WHERE id=?", (PH.hash('adminpasspres'), admin[0]))
        else:
            db.execute('INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                       (uid(), 'Admin', PH.hash('adminpasspres'), 'Administrator', now()))
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Banco de distribuicao invalido.')
        db.commit()
        db.execute('PRAGMA journal_mode=DELETE')


def copy_runtime(archive, destination):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != RUNTIME_HASH:
        raise RuntimeError('Checksum do Python portatil invalido.')
    destination.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise RuntimeError('Arquivo de runtime invalido.')
        bundle.extractall(destination)
    (destination / 'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n..\nimport site\n', encoding='utf-8')
    target = destination / 'Lib' / 'site-packages'
    versions = {}
    for name in PACKAGES:
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        for entry in distribution.files:
            if '..' in entry.parts or '__pycache__' in entry.parts or entry.suffix == '.pyc':
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            output = target / entry
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
    return versions


def build_windows(runtime, database, output, installer=True):
    release = (ROOT / 'VERSION').read_text().strip()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'data' / 'build-cache' / ('windows-' + uuid4().hex)
    work.mkdir(parents=True)
    name = 'ProducaoPresotto-' + release + '-windows-x64'
    package = work / name
    package.mkdir()
    for filename in ('app.py', 'updater.py', 'launcher.py', 'desktop.py', 'backup.py', 'VERSION', 'requirements.txt'):
        shutil.copyfile(ROOT / filename, package / filename)
    for directory in ('dist', 'migrations'):
        shutil.copytree(ROOT / directory, package / directory, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    versions = copy_runtime(runtime, package / 'runtime')
    (package / 'data').mkdir()
    seed_database(database, package / 'data' / 'kanban.sqlite3')
    for action, filename in (('start', 'Iniciar.cmd'), ('stop', 'Parar.cmd')):
        (package / filename).write_text('@echo off\r\nstart "" /D "%~dp0" "%~dp0runtime\\pythonw.exe" "%~dp0desktop.py" ' + action + '\r\n', encoding='ascii')
    shutil.copyfile(ROOT / 'packaging' / 'Instalar.ps1', package / 'Instalar.ps1')
    (package / 'Instalar.cmd').write_text('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar.ps1" %*\r\nexit /b %errorlevel%\r\n', encoding='ascii')
    (package / 'BUILD.json').write_text(json.dumps({'version': release, 'python': '3.13.17', 'architecture': 'x64', 'packages': versions}, indent=2), encoding='utf-8')
    binary = package / 'runtime' / 'python.exe'
    subprocess.run([str(binary), '-c', 'import flask,argon2,PIL,waitress,app; print("Bundled runtime OK")'], cwd=package, check=True)
    portable = output / (name + '.zip')
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                archive.write(path, name + '/' + path.relative_to(package).as_posix())
    build(ROOT, output / 'presotto-update.zip')
    if installer:
        bootstrap = work / 'bootstrap'
        bootstrap.mkdir()
        shutil.copyfile(portable, bootstrap / 'payload.zip')
        shutil.copyfile(package / 'Instalar.ps1', bootstrap / 'Instalar.ps1')
        shutil.copyfile(package / 'Instalar.cmd', bootstrap / 'Instalar.cmd')
        target = output / ('ProducaoPresotto-' + release + '-Setup.exe')
        sed = '\n'.join([
            '[Version]', 'Class=IEXPRESS', 'SEDVersion=3', '[Options]', 'PackagePurpose=InstallApp',
            'ShowInstallProgramWindow=0', 'HideExtractAnimation=0', 'UseLongFileName=1',
            'InsideCompressed=0', 'CAB_FixedSize=0', 'CAB_ResvCodeSigning=0', 'RebootMode=N',
            'InstallPrompt=', 'DisplayLicense=', 'FinishMessage=', 'TargetName=' + str(target),
            'FriendlyName=Producao Presotto ' + release, 'AppLaunched=cmd.exe /c Instalar.cmd',
            'PostInstallCmd=<None>', 'AdminQuietInstCmd=cmd.exe /c Instalar.cmd -Quiet',
            'UserQuietInstCmd=cmd.exe /c Instalar.cmd -Quiet', 'SourceFiles=SourceFiles', '[Strings]',
            'FILE0="payload.zip"', 'FILE1="Instalar.ps1"', 'FILE2="Instalar.cmd"', '[SourceFiles]',
            'SourceFiles0=' + str(bootstrap) + '\\', '[SourceFiles0]', '%FILE0%=', '%FILE1%=', '%FILE2%=', '',
        ])
        specification = work / 'installer.sed'
        specification.write_text(sed, encoding='ascii')
        subprocess.run([str(Path(os.environ['WINDIR']) / 'System32' / 'iexpress.exe'), '/N', '/Q', str(specification)], cwd=work, timeout=240, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        if not target.exists() or target.stat().st_size < portable.stat().st_size // 2:
            raise RuntimeError('O instalador nao foi gerado.')
    files = [p for p in output.iterdir() if p.suffix in ('.zip', '.exe')]
    (output / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in sorted(files)), encoding='ascii')
    print('Release:', output)
    print('Staging:', package)
    return package


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=ROOT / 'data/build-cache/python-3.13.17-embed-amd64.zip')
    parser.add_argument('--database', type=Path, default=ROOT / 'data/kanban.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT / 'releases' / (ROOT / 'VERSION').read_text().strip())
    parser.add_argument('--no-installer', action='store_true')
    args = parser.parse_args()
    build_windows(args.runtime.resolve(), args.database.resolve(), args.output, not args.no_installer)
