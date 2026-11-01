"""Read-only tracked-tree audit; writes reports only to the selected output directory.

Uses the standard library. Findings contain paths/line numbers, never secret values.
Run: python scripts/repository_audit.py --output .audit-local
"""
import argparse
import ast
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess


def git(*args):
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.audit-local'))
    args = parser.parse_args()
    root = Path(git('rev-parse', '--show-toplevel'))
    paths = git('ls-files', '-z').split('\0')
    texts = {}
    hashes = {}
    rows = []
    findings = []
    compile_errors = []
    for name in paths:
        path = root / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        hashes.setdefault(hashlib.sha256(data).hexdigest(), []).append(name)
        try:
            if b'\0' not in data:
                texts[name] = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            pass
        suffix = path.suffix.lower()
        generated = suffix in {'.stl', '.png', '.log', '.csg'} or '/output/' in name
        group = name.split('/')[0]
        purpose = {'assistant': 'AI, voice, display and device integration or regression',
                   'conversations': 'conversation persistence and memory',
                   'knowledge_base': 'document ingestion, retrieval and regression',
                   'system': 'resource monitoring, provisioning and regression',
                   'dashboard': 'dashboard view and regression',
                   'hardware': 'firmware, editable CAD, manufacturing or engineering evidence',
                   'docs': 'engineering documentation or acceptance evidence',
                   'deployment': 'operator-reviewed deployment template',
                   'scripts': 'independent utility or hardware entry point',
                   'templates': 'Django UI', 'static': 'browser assets',
                   'config': 'Django configuration'}.get(group, 'repository configuration or documentation')
        rows.append([name, suffix or 'extensionless', len(data), purpose, '',
                     'intentional generated evidence' if generated else 'source/config/documentation',
                     group, 'KEEP', 'tracked baseline; preserve behavior and acceptance/manufacturing evidence'])
    for row in rows:
        name = row[0]
        refs = [p for p, body in texts.items() if p != name and (name in body or (Path(name).name in body and len(Path(name).name) > 10))]
        row[4] = '; '.join(refs[:5]) or 'no textual reference found; imports/entry points may be independent'
    patterns = {
        'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'token-shape': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b',
        'credential-assignment-review': r'(?i)(?:password|secret_key|api_key|token|wifi_ssid)\s*[:=]\s*[\"\'][^\"\']{4,}[\"\']',
        'private-network-review': r'\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b',
    }
    for name, body in texts.items():
        if name.endswith('.py'):
            try:
                ast.parse(body, filename=name)
            except SyntaxError as exc:
                compile_errors.append({'path': name, 'line': exc.lineno})
        for number, line in enumerate(body.splitlines(), 1):
            for kind, pattern in patterns.items():
                if re.search(pattern, line):
                    findings.append({'path': name, 'line': number, 'kind': kind})
    branches = []
    for branch in git('for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin').splitlines():
        if branch in {'origin', 'origin/HEAD'}:
            continue
        counts = git('rev-list', '--left-right', '--count', 'origin/main...' + branch).split()
        branches.append({'branch': branch, 'main_only': int(counts[0]), 'branch_only': int(counts[1]), 'sha': git('rev-parse', branch)})
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'inventory.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['path', 'type', 'bytes', 'purpose', 'textual_references', 'tracking', 'needed_for', 'disposition', 'evidence'])
        writer.writerows(rows)
    summary = {'baseline': git('rev-parse', 'origin/main'), 'files': len(rows),
               'bytes': sum(r[2] for r in rows), 'python_files_parsed': sum(p.endswith('.py') for p in texts),
               'syntax_errors': compile_errors, 'duplicates': [v for v in hashes.values() if len(v) > 1],
               'security_candidates': findings, 'branches': branches,
               'largest': sorted([{'path': r[0], 'bytes': r[2]} for r in rows], key=lambda r: r['bytes'], reverse=True)[:20]}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('files', 'bytes', 'python_files_parsed', 'syntax_errors')}))
    print('Security candidates require manual review; this heuristic is not a full history scan.')


if __name__ == '__main__':
    main()
