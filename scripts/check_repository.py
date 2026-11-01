"""Check tracked Python syntax and maintained Markdown file links, without imports.

Historical acceptance/CAD documents are intentionally outside the maintained-link
set; their missing generated ZIPs are not public downloads. Anchors and external
URLs are outside this file-existence check. No network or hardware access occurs.
"""
import ast
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


def check(root):
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root)
    paths = [Path(p.decode('utf-8')) for p in raw.split(b'\0') if p]
    # Include new source during pre-commit validation as well as tracked source.
    raw = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=root)
    paths += [Path(p.decode('utf-8')) for p in raw.split(b'\0') if p]
    available = {(root / path).resolve() for path in paths if (root / path).exists()}
    errors = []
    python_count = link_count = 0
    for relative in sorted(set(paths)):
        path = root / relative
        if path.suffix == '.py':
            python_count += 1
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(relative))
            except (SyntaxError, UnicodeError) as exc:
                errors.append(f'{relative}: {type(exc).__name__}')
        maintained = (relative.as_posix() == 'README.md'
                      or (relative.parent == Path('docs') and relative.stem.isupper())
                      or relative.as_posix() == 'docs/media/README.md')
        if path.suffix != '.md' or not maintained:
            continue
        body = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8-sig'), flags=re.S)
        for target in re.findall(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)', body):
            if target.startswith(('https:', 'http:', 'mailto:', '#')):
                continue
            target = unquote(target.strip('<>').split('#')[0])
            if target:
                link_count += 1
                destination = (path.parent / target).resolve()
                if destination not in available and not (
                        destination.is_dir() and any(p.is_relative_to(destination) for p in available)):
                    errors.append(f'{relative}: missing target {target}')
    print(f'Parsed {python_count} Python files; checked {link_count} maintained local file links.')
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(check(Path(__file__).resolve().parents[1]))
