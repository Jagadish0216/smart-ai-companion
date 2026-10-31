"""Hash-checked preparation/apply/rollback. Never restarts or changes networking."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_manifest():
    data = (HERE / 'manifest.json').read_bytes()
    manifest = json.loads(data)
    if digest((HERE / 'update.patch').read_bytes()) != manifest['patch_sha256']:
        raise RuntimeError('Package patch hash mismatch.')
    return manifest, digest(data)


def project_path(root, name):
    path = root / name
    if path.is_symlink():
        raise RuntimeError(f'Refusing symlink: {name}')
    path.resolve().relative_to(root.resolve())
    return path


def verify(root, hashes):
    for name, expected in hashes.items():
        path = project_path(root, name)
        actual = digest(path.read_bytes()) if path.is_file() else None
        if actual != expected:
            raise RuntimeError(f'Source differs from the reviewed state: {name}')


def run(args, cwd=None, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def check(root, require_git=True):
    manifest, _ = read_manifest()
    if require_git:
        for args, expected in ((['branch', '--show-current'], manifest['branch']),
                               (['rev-parse', 'HEAD'], manifest['head'])):
            value = subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            if value != expected:
                raise RuntimeError('Branch/HEAD changed since the source snapshot. No files changed.')
    verify(root, manifest['before'])
    verify(root, manifest['preserved'])
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', '--check', '--whitespace=error', HERE / 'update.patch'], cwd=root)
    print('Compatible with reviewed Pi source; existing provider/configuration files match.')


def validate(root, stage, python, require_git=True):
    check(root, require_git=require_git)
    manifest, manifest_hash = read_manifest()
    stage = stage.absolute()
    if stage.exists():
        raise RuntimeError('Staging directory already exists; choose a new directory.')
    if stage == root or root in stage.parents:
        raise RuntimeError('Staging must be outside the production project.')
    stage.mkdir(mode=0o700, parents=False)
    # Copy only the reviewed snapshot paths. Never copy .env, live databases,
    # credentials, Git metadata, virtualenvs or production media.
    paths = {**manifest['preserved'], **manifest['before']}
    for name, expected in paths.items():
        if expected is None:
            continue
        source = project_path(root, name)
        data = source.read_bytes()
        if digest(data) != expected:
            raise RuntimeError(f'Source changed during staging: {name}')
        target = project_path(stage, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', '--check', HERE / 'update.patch'], cwd=stage)
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', HERE / 'update.patch'], cwd=stage)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1')
    # Match the Pi snapshot's test settings; all databases/media are disposable.
    # The runner also forbids socket connections and checks Django configuration.
    run([python, HERE / 'verify_suite.py', stage, '--report', stage / 'test-report.json', '--exclusions', HERE / 'unavailable-tests.json'], env=env)
    verify(stage, manifest['after'])
    verify(stage, manifest['preserved'])
    check(root, require_git=require_git)
    record = {'manifest_sha256': manifest_hash, 'stage': str(stage.resolve()),
              'tests_passed': True, 'python': str(python)}
    (stage / 'demo-validation.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Staged tests passed. Production project, services and networking were not changed.')


def apply(root, stage, backup, require_git=True):
    check(root, require_git=require_git)
    manifest, manifest_hash = read_manifest()
    record = json.loads((stage / 'demo-validation.json').read_text(encoding='utf-8'))
    if record.get('manifest_sha256') != manifest_hash or record.get('tests_passed') is not True:
        raise RuntimeError('A successful staged validation of this exact package is required.')
    verify(stage, manifest['after'])
    verify(stage, manifest['preserved'])
    if backup.exists():
        raise RuntimeError('Backup already exists; choose a new backup path.')
    if backup == root or root in backup.parents:
        raise RuntimeError('Backup must be outside the production project.')
    backup.mkdir(mode=0o700, parents=False)
    for name, expected in manifest['before'].items():
        if expected is not None:
            target = project_path(backup / 'original', name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_path(root, name), target)
    shutil.copy2(HERE / 'update.patch', backup / 'update.patch')
    shutil.copy2(HERE / 'manifest.json', backup / 'manifest.json')
    # Git apply validates all hunks and applies atomically without --reject,
    # staging, committing, replacing whole files, or resetting any existing work.
    check(root, require_git=require_git)
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', '--whitespace=error', HERE / 'update.patch'], cwd=root)
    verify(root, manifest['after'])
    verify(root, manifest['preserved'])
    (backup / 'applied.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(f'Patch applied; rollback backup: {backup}')
    print('No service restart, model warm-up, migration, network operation or reboot was performed.')


def rollback(root, backup):
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    patch = backup / 'update.patch'
    if digest(patch.read_bytes()) != manifest['patch_sha256']:
        raise RuntimeError('Rollback patch hash mismatch.')
    if not (backup / 'applied.json').is_file():
        raise RuntimeError('No completed application recorded in this backup.')
    record = json.loads((backup / 'applied.json').read_text(encoding='utf-8'))
    if digest((backup / 'manifest.json').read_bytes()) != record.get('manifest_sha256'):
        raise RuntimeError('Rollback manifest hash mismatch.')
    verify(root, manifest['after'])
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', '--reverse', '--check', patch], cwd=root)
    run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', 'apply', '--reverse', patch], cwd=root)
    verify(root, manifest['before'])
    print('Only the demo patch was reversed; pre-existing uncommitted work is preserved.')
    print('Services and networking were not changed. Running processes may retain loaded code.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'validate', 'apply', 'rollback'])
    parser.add_argument('--project', type=Path, default=Path('/home/jagadish/apps/smart-ai-companion'))
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--python', default='/home/jagadish/apps/smart-ai-companion/venv/bin/python')
    args = parser.parse_args()
    root = args.project.resolve(strict=True)
    if args.action in ('validate', 'apply') and args.stage is None:
        parser.error('--stage is required')
    if args.action in ('apply', 'rollback') and args.backup is None:
        parser.error('--backup is required')
    if args.action == 'check':
        check(root)
    elif args.action == 'validate':
        validate(root, args.stage.absolute(), args.python)
    elif args.action == 'apply':
        apply(root, args.stage.absolute(), args.backup.absolute())
    else:
        rollback(root, args.backup.absolute())


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'STOPPED: {exc}', file=sys.stderr)
        sys.exit(1)
