"""Build a release asset, without data, environment files or credentials."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from updater import allowed, version


def build(source, output, minimum='1.0.0'):
    source = Path(source)
    installed = (source / 'VERSION').read_text().strip()
    version(installed)
    version(minimum)
    paths = [source / name for name in ('app.py', 'updater.py', 'VERSION', 'requirements.txt')]
    for directory in ('dist', 'migrations'):
        paths.extend(p for p in (source / directory).rglob('*') if p.is_file())
    files = {p.relative_to(source).as_posix(): p.read_bytes() for p in paths if allowed(p.relative_to(source).as_posix())}
    manifest = {'protocol': 1, 'version': installed, 'minimum_version': minimum,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('release.json', json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)


if __name__ == '__main__':
    build(Path(__file__).resolve().parent, sys.argv[1] if len(sys.argv) > 1 else 'pressotto-update.zip')
