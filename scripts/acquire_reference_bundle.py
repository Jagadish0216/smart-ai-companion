"""Restore pinned licensed references; existing matching files never download again.
New versions require a reviewed manifest hash. Run bootstrap_knowledge afterward.
"""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Reference redirects require manifest review.')

def acquire(root):
    root = Path(root).resolve()
    data = json.loads((root/'manifest.json').read_text())
    report = []
    deadline = time.monotonic()+120
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for entry in data['sources']:
        if entry['status'] != 'BUNDLED':
            report.append({'key':entry['key'], 'status':'UNPOPULATED', 'reason':entry.get('reason')})
            continue
        try:
            for field, url_field, hash_field in [('license_file','license_url','license_sha256'), ('path','url','sha256')]:
                path = (root/entry[field]).resolve()
                path.relative_to(root)
                expected = entry[hash_field]
                if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                    continue
                url = entry[url_field]
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname != 'raw.githubusercontent.com' or parsed.username or parsed.port:
                    raise ValueError('Unapproved source URL')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Acquisition budget exhausted; resume next invocation')
                request = urllib.request.Request(url, headers={'User-Agent':'SmartAICompanion-ReferenceImporter/1.0'})
                with opener.open(request, timeout=min(15, deadline-time.monotonic())) as response:
                    payload = response.read(300001)
                if len(payload)>300000 or hashlib.sha256(payload).hexdigest()!=expected:
                    raise ValueError('Pinned checksum mismatch; retained old source')
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.download')
                temporary.write_bytes(payload)
                temporary.replace(path)
                time.sleep(1)
            report.append({'key':entry['key'], 'status':'ACQUIRED'})
        except Exception as exc:
            report.append({'key':entry['key'], 'status':'FAILED', 'reason':type(exc).__name__})
    return report

if __name__ == '__main__':
    report = acquire(Path(__file__).resolve().parents[1]/'knowledge_library')
    print(json.dumps(report, indent=2))
    raise SystemExit(any(row['status']=='FAILED' for row in report))
