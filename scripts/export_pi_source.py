"""Export allowlisted working-tree source and hashes without operating the device.

Run locally ON the Pi only after owner review, writing outside the source tree:
python scripts/export_pi_source.py --output /home/jagadish/pi-source-review.zip
The archive is private review input, not an automatically publishable artifact.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


ROOT_FILES = {'manage.py', 'smoke_voice.py', 'requirements.txt'}
SOURCE_ROOTS = {'assistant', 'config', 'conversations', 'dashboard', 'knowledge_base',
                'system', 'scripts', 'templates', 'static', 'deployment', 'firmware', 'tests'}
EXTENSIONS = {'.py', '.html', '.css', '.js', '.cjs', '.svg', '.sh', '.service',
              '.timer', '.rules', '.conf', '.ino', '.h', '.cpp'}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
TOKEN = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})')
KEY = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
ASSIGNMENT = re.compile(
    rb'''(?im)^\s*(?:#define\s+)?(?:API_KEY|SECRET_KEY|PASSWORD|WIFI_PASSWORD|MQTT_PASSWORD|ACCESS_TOKEN)\s*(?:=\s*|\s+)["']([^"'\r\n]+)["']''')


class ExportError(Exception):
    pass


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'), timeout=30)
    if result.returncode:
        raise ExportError('Git inspection failed; no source was changed.')
    return result.stdout


def allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        return False
    if any(part.startswith('.') or part.lower() in {'media', 'logs', 'venv', '__pycache__'}
           for part in path.parts):
        return False
    lower = path.name.lower()
    if any(word in lower for word in ('secret', 'credential', 'private', 'backup')):
        return False
    if name in ROOT_FILES:
        return True
    in_source = path.parts[0] in SOURCE_ROOTS or path.parts[:2] == ('hardware', 'esp32')
    return in_source and path.suffix.lower() in EXTENSIONS


def check_content(name, data):
    if b'\0' in data:
        raise ExportError(f'Unexpected binary source excluded; review required: {name}')
    if TOKEN.search(data) or KEY.search(data):
        raise ExportError(f'Possible credential detected; export refused: {name}')
    # Test fixtures are allowed to contain dummy assignments, but still undergo
    # high-confidence token/private-key checks. Production literals fail closed.
    if not PurePosixPath(name).name.startswith(('test_', 'tests')):
        for match in ASSIGNMENT.finditer(data):
            literal = match[1].lower()
            if not (literal.startswith((b'your-', b'example', b'placeholder', b'change-me'))
                    or literal == b'django-insecure-default-key-for-dev'):
                raise ExportError(f'Hardcoded credential-like assignment needs private review: {name}')


def export(root, output):
    root = root.resolve()
    output = output.absolute()
    if output.parent.resolve().is_relative_to(root):
        raise ExportError('Choose an output outside the source tree.')
    if not output.parent.is_dir():
        raise ExportError('Output parent must already exist.')
    head = git(root, 'rev-parse', 'HEAD').decode('ascii').strip()
    before = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    records = {}
    content = {}
    excluded = 0
    total = 0
    for encoded in sorted(set(names.split(b'\0'))):
        if not encoded:
            continue
        name = encoded.decode('utf-8')
        if not allowed(name):
            excluded += 1
            continue
        path = root / name
        # Reject links, including an intermediate directory link out of the tree.
        if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
            raise ExportError(f'Symlink source requires private review: {name}')
        if not path.is_file():
            records[name] = {'status': 'missing'}
            continue
        stat = path.stat()
        if stat.st_size > MAX_FILE_BYTES:
            raise ExportError(f'Source exceeds the per-file limit: {name}')
        data = path.read_bytes()
        if stat.st_mtime_ns != path.stat().st_mtime_ns or stat.st_size != len(data):
            raise ExportError('Source changed during export; retry when editing is idle.')
        check_content(name, data)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ExportError('Source exceeds the total export limit.')
        records[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        content[name] = data
    after = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    if before != after or git(root, 'rev-parse', 'HEAD').decode('ascii').strip() != head:
        raise ExportError('Git state changed during export; retry when editing is idle.')
    # Preserve status codes for exported paths only, not private/unrelated names.
    status = []
    entries = iter(before.split(b'\0'))
    for item in entries:
        if not item:
            continue
        code, name = item[:2].decode('ascii'), item[3:].decode('utf-8')
        if 'R' in code or 'C' in code:
            next(entries, None)  # Original name is unnecessary private metadata.
        if name in records:
            status.append({'path': name, 'status': code})
    manifest = {
        'format': 1, 'git_head': head, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'working_tree_status': status, 'excluded_path_count': excluded,
        'source_bytes': total, 'files': records,
        'limitations': 'Secrets/environment/databases/logs/media excluded by allowlist; '
                       'ignored files not exported. Heuristic source scanning is not '
                       'proof of privacy. Review privately before sharing. No .env '
                       'or RAG runtime setting is captured. No services are operated.',
    }
    created = False
    try:
        # Exclusive creation prevents replacing an existing archive or symlink.
        with output.open('xb') as handle:
            created = True
            os.chmod(output, 0o600)
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in content.items():
                    archive.writestr('source/' + name, data)
                archive.writestr('MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    except Exception:
        if created:
            output.unlink(missing_ok=True)  # Only our newly created partial output.
        raise
    return {'files': len(content), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.root, args.output)))
    except (ExportError, FileExistsError) as exc:
        parser.exit(1, f'Export refused: {exc}\n')


if __name__ == '__main__':
    main()
