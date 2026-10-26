"""SQLite-consistent maintenance backup and non-destructive storage restore.

Application/voice writers must be stopped by the caller. Restore keeps newly
created uploads and saves the displaced database for recovery.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time


@contextmanager
def connection(path, **kwargs):
    db = sqlite3.connect(path, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def backup(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Backup destination must be new.')
    files = []
    for name in ('media', 'knowledge', '.env'):
        path = root/name
        if path.is_symlink():
            raise ValueError('Protected storage symlink requires a separately reviewed backup.')
        files.extend([path] if path.is_file() else list(path.rglob('*')) if path.exists() else [])
    if any(p.is_symlink() for p in files):
        raise ValueError('Storage symlinks require a separately reviewed backup.')
    required = sum(p.stat().st_size for p in files if p.is_file()) + (root/'db.sqlite3').stat().st_size
    if shutil.disk_usage(destination.parent).free < required*2 + 512*1024*1024:
        raise ValueError('Insufficient free disk for backup and deployment.')
    destination.mkdir(mode=0o700)
    with connection(f'file:{(root/"db.sqlite3").as_posix()}?mode=ro', uri=True) as src, connection(destination/'db.sqlite3') as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup database integrity check failed.')
    for path in files:
        if path.is_file():
            target = destination/path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    hashes = {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob('*') if p.is_file()}
    (destination/'backup.json').write_text(json.dumps({'root':str(root), 'hashes':hashes}, indent=2))


def restore(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    manifest = json.loads((destination/'backup.json').read_text())
    if str(root) != manifest['root']:
        raise ValueError('Backup belongs to another checkout.')
    for name, expected in manifest['hashes'].items():
        source = (destination/name).resolve()
        source.relative_to(destination)
        if digest(source) != expected:
            raise ValueError('Backup checksum mismatch.')
    # Keep the post-deployment database, including any subsequent user writes.
    recovery = root/f'knowledge-rollback-recovery-{time.time_ns()}.sqlite3'
    with connection(root/'db.sqlite3') as src, connection(recovery) as dst:
        src.backup(dst)
    with connection(destination/'db.sqlite3') as src, connection(root/'db.sqlite3') as dst:
        src.backup(dst)
    for name in manifest['hashes']:
        if name in {'db.sqlite3', '.env'}:
            continue  # Environment is preserved, never overwritten on rollback.
        target = (root/name).resolve()
        target.relative_to(root)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination/name, target)
    return recovery


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('root')
    parser.add_argument('destination')
    args = parser.parse_args()
    result = globals()[args.action](args.root, args.destination)
    if result:
        print('Preserved post-deployment database:', result)
