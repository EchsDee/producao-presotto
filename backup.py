"""Create a consistent SQLite backup, including attachment bytes, while the app runs."""
import argparse
import os
import sqlite3
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError('Source database does not exist.')
    if source == destination:
        raise ValueError('Choose a different backup location.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally replacing an existing backup.
    with destination.open('xb'):
        pass
    try:
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        try:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed.')
            finally:
                dst.close()
        finally:
            src.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', help='New backup file; existing files are never overwritten')
    parser.add_argument('--source', default=os.environ.get('KANBAN_DATABASE', str(Path(__file__).parent / 'data' / 'kanban.sqlite3')))
    args = parser.parse_args()
    print(f'Backup saved: {backup(args.source, args.destination)}')
