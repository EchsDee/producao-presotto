"""One-time SSH bootstrap: replace only updater.py from a verified release ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile


def enable(root, archive, expected):
    root = Path(root).resolve()
    restart = root.parent / 'app.wsgi'
    if root.name != 'app' or not restart.is_file() or restart.is_symlink():
        raise ValueError('Expected app/ alongside an existing app.wsgi.')
    raw = Path(archive).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected.lower():
        raise ValueError('ZIP SHA-256 mismatch; no files changed.')
    with zipfile.ZipFile(archive) as bundle:
        source = bundle.read('updater.py')
        manifest = json.loads(bundle.read('release.json'))
        if hashlib.sha256(source).hexdigest() != manifest['files']['updater.py']:
            raise ValueError('Invalid updater checksum.')
    compile(source, 'updater.py', 'exec')
    target = root / 'updater.py'
    if target.is_symlink():
        raise ValueError('Symlink target not supported.')
    saved = root.parent / ('updater-before-wsgi-' + str(time.time_ns()) + '.py')
    with saved.open('xb') as output:
        output.write(target.read_bytes())
    temporary = root / ('.updater-bootstrap-' + str(time.time_ns()))
    try:
        temporary.write_bytes(source)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    restart.touch()
    print('WSGI updater enabled. Backup:', saved)
    print('Reload the site, download the release again and select Install update.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'apps_wsgi' / 'app')
    args = parser.parse_args()
    enable(args.root, args.archive, args.sha256)
